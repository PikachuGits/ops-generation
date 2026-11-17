from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, JSON, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JsonDocument = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class OpsGenerationJob(Base):
    __tablename__ = "ops_generation_jobs"
    __table_args__ = (
        CheckConstraint("status IN ('queued', 'drafted', 'failed')", name="ck_ops_generation_jobs_status"),
        Index("ix_ops_generation_scope_created", "org_id", "site_id", "created_at"),
        Index("ix_ops_generation_created_by", "created_by", "created_at"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    org_id: Mapped[str] = mapped_column(String(128), nullable=False)
    site_id: Mapped[str] = mapped_column(String(128), nullable=False)
    function_code: Mapped[str] = mapped_column(String(64), nullable=False)
    input_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued", index=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    draft_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lint_issues: Mapped[list[dict[str, Any]]] = mapped_column(JsonDocument, nullable=False, default=list)
    inputs: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False)
    generated_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    template_version: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OpsGenerationIdempotency(Base):
    __tablename__ = "ops_generation_idempotency"
    __table_args__ = (Index("ix_ops_generation_idempotency_expires_at", "expires_at"),)

    scope_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
