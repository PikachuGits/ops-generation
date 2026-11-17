from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.clients.gateway import GatewayClient
from app.clients.review_queue import ReviewQueueClient
from app.config import Settings
from app.errors import FALLBACK_ERROR_MESSAGE, GatewayFailure, RequestInputError, ReviewQueueUnavailable, user_message
from app.idempotency import canonical_json, input_digest, scoped_key
from app.linting import lint_content
from app.models import OpsGenerationIdempotency, OpsGenerationJob
from app.queueing import JobDispatcher
from app.templates import PromptTemplate, get_template, validate_required_fields


@dataclass(frozen=True, slots=True)
class UserContext:
    org_id: str
    site_id: str
    user_id: str


@dataclass(slots=True)
class JobResult:
    job_id: str
    status: str
    function_code: str
    input_sha256: str
    draft_id: str | None
    error_code: str | None
    lint_issues: list[dict[str, Any]]
    attempt_count: int
    generated_content: str | None = None
    message: str | None = None


class OpsGenerationService:
    """Shared generation pipeline; every business function enters through run()."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        gateway: GatewayClient,
        review_queue: ReviewQueueClient,
        redis_client: Any | None,
        dispatcher: JobDispatcher,
        settings: Settings,
    ) -> None:
        self.session_factory = session_factory
        self.gateway = gateway
        self.review_queue = review_queue
        self.redis = redis_client
        self.dispatcher = dispatcher
        self.settings = settings

    def run(self, function_code: str, inputs: dict[str, Any], user: UserContext) -> JobResult:
        template = get_template(function_code)
        normalized = self._normalize_inputs(inputs)
        validate_required_fields(template, normalized)
        normalized, warnings = self._truncate_inputs(normalized, min(template.max_chars, self.settings.max_input_chars))
        digest = input_digest(function_code, normalized)
        key = scoped_key(user.org_id, user.site_id, user.user_id, function_code, digest)
        candidate_id = str(uuid.uuid4())
        cache_key = f"opsgen:idem:{key}"
        cache_claimed = self._claim_idempotency_cache(cache_key, candidate_id)

        now = datetime.now(timezone.utc)
        job = OpsGenerationJob(
            id=candidate_id,
            org_id=user.org_id,
            site_id=user.site_id,
            function_code=function_code,
            input_sha256=digest,
            status="queued",
            error_code=None,
            draft_id=None,
            lint_issues=warnings,
            inputs=normalized,
            generated_content=None,
            template_version=template.version,
            attempt_count=0,
            created_by=user.user_id,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        try:
            job_id, created = self._reserve_job(
                job=job,
                scope_key=key,
                expires_at=now + timedelta(seconds=self.settings.idempotency_ttl_seconds),
            )
        except Exception:
            if cache_claimed:
                self._release_cache_claim(cache_key, candidate_id)
            raise

        if not created:
            if cache_claimed:
                self._release_cache_claim(cache_key, candidate_id)
            return self.get_job_by_id(job_id) or self._pending_result(job_id, function_code, digest)

        # Redis SET NX is the fast distributed claim; the transactional database record
        # remains the correctness fallback when Redis is unavailable.
        if self.redis is not None and not cache_claimed:
            try:
                self.redis.set(cache_key, candidate_id, ex=self.settings.idempotency_ttl_seconds, nx=True)
            except RedisError:
                pass

        if template.execution_mode == "async":
            try:
                self.dispatcher.enqueue(job_id)
            except Exception:
                # Preserve queued state. A scoped retry request or worker redrive can resume it.
                pass
            return self.get_job_by_id(job_id) or self._pending_result(job_id, function_code, digest)

        return self.process_job(job_id)

    def process_job(self, job_id: str) -> JobResult:
        lock_key = f"opsgen:process:{job_id}"
        lock_token = str(uuid.uuid4())
        lock_claimed = self._claim_processing_lock(lock_key, lock_token)
        if not lock_claimed:
            return self.get_job_by_id(job_id) or self._pending_result(job_id, "", "")

        try:
            snapshot = self._begin_attempt(job_id)
            if snapshot is None:
                return self.get_job_by_id(job_id) or self._pending_result(job_id, "", "")
            if snapshot["attempt_count"] > self.settings.max_attempts:
                return self.fail_exhausted_job(job_id)

            content = snapshot["generated_content"]
            lint_issues = snapshot["lint_issues"]
            if content is None:
                template = get_template(snapshot["function_code"])
                try:
                    generated = self.gateway.generate(
                        function_code=snapshot["function_code"],
                        messages=template.render(snapshot["inputs"]),
                    )
                    content = self._validate_gateway_payload(generated)
                except GatewayFailure as exc:
                    return self._mark_failed(job_id, exc.error_code)
                except Exception:
                    return self._mark_failed(job_id, "ALL_PROVIDERS_FAILED")

                lint_issues = [*lint_issues, *lint_content(content)]
                self._save_generated_checkpoint(job_id, content, lint_issues)

            try:
                draft_id = self.review_queue.enqueue_draft(
                    job_id=job_id,
                    org_id=snapshot["org_id"],
                    site_id=snapshot["site_id"],
                    created_by=snapshot["created_by"],
                    function_code=snapshot["function_code"],
                    content=content,
                    lint_issues=lint_issues,
                )
            except ReviewQueueUnavailable:
                if snapshot["attempt_count"] >= self.settings.max_attempts:
                    return self._mark_failed(job_id, "ALL_PROVIDERS_FAILED")
                return self.get_job_by_id(job_id) or self._pending_result(job_id, snapshot["function_code"], snapshot["input_sha256"])
            except Exception:
                if snapshot["attempt_count"] >= self.settings.max_attempts:
                    return self._mark_failed(job_id, "ALL_PROVIDERS_FAILED")
                return self.get_job_by_id(job_id) or self._pending_result(job_id, snapshot["function_code"], snapshot["input_sha256"])

            with self.session_factory() as session:
                with session.begin():
                    row = session.get(OpsGenerationJob, job_id)
                    if row is None or row.deleted_at is not None:
                        return self._pending_result(job_id, snapshot["function_code"], snapshot["input_sha256"])
                    if row.status == "queued":
                        row.status = "drafted"
                        row.draft_id = draft_id
                        row.error_code = None
                        row.updated_at = datetime.now(timezone.utc)
            return self.get_job_by_id(job_id) or self._pending_result(job_id, snapshot["function_code"], snapshot["input_sha256"])
        finally:
            if lock_claimed:
                self._release_cache_claim(lock_key, lock_token)

    def retry_queued(self, job_id: str, user: UserContext) -> JobResult | None:
        result = self.get_job(job_id, user)
        if result is None:
            return None
        if result.status == "queued":
            try:
                self.dispatcher.enqueue(job_id)
            except Exception:
                pass
        return self.get_job_by_id(job_id) or result

    def get_job(self, job_id: str, user: UserContext) -> JobResult | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(OpsGenerationJob).where(
                    OpsGenerationJob.id == job_id,
                    OpsGenerationJob.org_id == user.org_id,
                    OpsGenerationJob.site_id == user.site_id,
                    OpsGenerationJob.created_by == user.user_id,
                    OpsGenerationJob.deleted_at.is_(None),
                )
            )
            return self._to_result(row) if row is not None else None

    def get_job_by_id(self, job_id: str) -> JobResult | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(OpsGenerationJob).where(
                    OpsGenerationJob.id == job_id,
                    OpsGenerationJob.deleted_at.is_(None),
                )
            )
            return self._to_result(row) if row is not None else None

    def fail_exhausted_job(self, job_id: str) -> JobResult:
        return self._mark_failed(job_id, "ALL_PROVIDERS_FAILED")

    def _reserve_job(self, *, job: OpsGenerationJob, scope_key: str, expires_at: datetime) -> tuple[str, bool]:
        for attempt in range(3):
            try:
                with self.session_factory() as session:
                    with session.begin():
                        record = session.get(OpsGenerationIdempotency, scope_key, with_for_update=True)
                        now = datetime.now(timezone.utc)
                        if record is not None and self._as_utc(record.expires_at) > now:
                            existing = session.get(OpsGenerationJob, record.job_id)
                            if existing is not None and existing.deleted_at is None:
                                return record.job_id, False
                        if record is None:
                            session.add(OpsGenerationIdempotency(scope_key=scope_key, job_id=job.id, expires_at=expires_at))
                        else:
                            record.job_id = job.id
                            record.expires_at = expires_at
                            record.updated_at = now
                        session.add(job)
                    return job.id, True
            except IntegrityError:
                if attempt == 2:
                    raise
        raise RuntimeError("无法建立幂等记录")

    def _claim_idempotency_cache(self, key: str, job_id: str) -> bool:
        if self.redis is None:
            return False
        try:
            return bool(self.redis.set(key, job_id, ex=self.settings.idempotency_ttl_seconds, nx=True))
        except RedisError:
            return False

    def _claim_processing_lock(self, key: str, token: str) -> bool:
        if self.redis is None:
            return True
        try:
            return bool(self.redis.set(key, token, ex=60, nx=True))
        except RedisError:
            return True

    def _release_cache_claim(self, key: str, token: str) -> None:
        if self.redis is None:
            return
        try:
            self.redis.eval(
                "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
                1,
                key,
                token,
            )
        except AttributeError:
            try:
                if self.redis.get(key) == token:
                    self.redis.delete(key)
            except RedisError:
                pass
        except RedisError:
            pass

    def _begin_attempt(self, job_id: str) -> dict[str, Any] | None:
        with self.session_factory() as session:
            with session.begin():
                row = session.get(OpsGenerationJob, job_id, with_for_update=True)
                if row is None or row.deleted_at is not None or row.status != "queued":
                    return None
                row.attempt_count += 1
                row.updated_at = datetime.now(timezone.utc)
                return {
                    "org_id": row.org_id,
                    "site_id": row.site_id,
                    "created_by": row.created_by,
                    "function_code": row.function_code,
                    "input_sha256": row.input_sha256,
                    "inputs": row.inputs,
                    "generated_content": row.generated_content,
                    "lint_issues": list(row.lint_issues or []),
                    "attempt_count": row.attempt_count,
                }

    def _save_generated_checkpoint(self, job_id: str, content: str, lint_issues: list[dict[str, Any]]) -> None:
        with self.session_factory() as session:
            with session.begin():
                row = session.get(OpsGenerationJob, job_id, with_for_update=True)
                if row is not None and row.status == "queued" and row.deleted_at is None:
                    row.generated_content = content
                    row.lint_issues = lint_issues
                    row.updated_at = datetime.now(timezone.utc)

    def _mark_failed(self, job_id: str, error_code: str) -> JobResult:
        with self.session_factory() as session:
            with session.begin():
                row = session.get(OpsGenerationJob, job_id, with_for_update=True)
                if row is not None and row.status == "queued" and row.deleted_at is None:
                    row.status = "failed"
                    row.error_code = error_code
                    row.updated_at = datetime.now(timezone.utc)
        return self.get_job_by_id(job_id) or self._pending_result(job_id, "", "")

    def _validate_gateway_payload(self, payload: dict[str, Any]) -> str:
        if not isinstance(payload, dict) or set(payload) != {"content"} or not isinstance(payload.get("content"), str):
            raise GatewayFailure("SCHEMA_VALIDATION_FAILED")
        content = payload["content"]
        if not content.strip():
            raise GatewayFailure("SCHEMA_VALIDATION_FAILED")
        return content

    def _normalize_inputs(self, inputs: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(inputs, dict) or not inputs:
            raise RequestInputError("inputs 不能为空")
        try:
            return json.loads(canonical_json(inputs))
        except (TypeError, ValueError) as exc:
            raise RequestInputError("inputs 必须是有效 JSON") from exc

    def _truncate_inputs(self, inputs: dict[str, Any], max_chars: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        warnings: list[dict[str, Any]] = []
        while len(canonical_json(inputs)) > max_chars:
            strings = self._string_paths(inputs)
            if not strings:
                raise RequestInputError("inputs 超过模板长度限制且无法安全截断")
            path, current = max(strings, key=lambda item: len(item[1]))
            excess = len(canonical_json(inputs)) - max_chars
            new_length = max(0, len(current) - max(1, excess))
            self._set_path(inputs, path, current[:new_length])
            warnings = [{"code": "INPUT_TRUNCATED", "message": "输入超过模板限制，部分文本已截断，请人工核对"}]
        return inputs, warnings

    def _string_paths(self, value: Any, path: tuple[Any, ...] = ()) -> list[tuple[tuple[Any, ...], str]]:
        if isinstance(value, str):
            return [(path, value)] if value else []
        if isinstance(value, dict):
            return [item for key, child in value.items() for item in self._string_paths(child, (*path, key))]
        if isinstance(value, list):
            return [item for index, child in enumerate(value) for item in self._string_paths(child, (*path, index))]
        return []

    def _set_path(self, value: Any, path: tuple[Any, ...], replacement: str) -> None:
        parent = value
        for key in path[:-1]:
            parent = parent[key]
        parent[path[-1]] = replacement

    def _to_result(self, row: OpsGenerationJob) -> JobResult:
        if row.status == "failed":
            message = user_message(row.error_code) or FALLBACK_ERROR_MESSAGE
        elif row.status == "drafted":
            message = "草稿已进入复核队列，等待人工复核"
        else:
            message = "任务已排队，等待处理"
        return JobResult(
            job_id=row.id,
            status=row.status,
            function_code=row.function_code,
            input_sha256=row.input_sha256,
            draft_id=row.draft_id,
            error_code=row.error_code,
            lint_issues=list(row.lint_issues or []),
            attempt_count=row.attempt_count,
            generated_content=row.generated_content,
            message=message,
        )

    def _pending_result(self, job_id: str, function_code: str, digest: str) -> JobResult:
        return JobResult(
            job_id=job_id,
            status="queued",
            function_code=function_code,
            input_sha256=digest,
            draft_id=None,
            error_code=None,
            lint_issues=[],
            attempt_count=0,
            message="任务已排队，等待处理",
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
