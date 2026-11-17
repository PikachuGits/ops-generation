from __future__ import annotations

from functools import lru_cache
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.config import Settings
from app.runtime import Runtime, build_runtime
from app.schemas import ErrorResponse, GenerationRequest, GenerationResponse, JobResponse
from app.service import JobResult, UserContext
from app.errors import RequestInputError


@lru_cache(maxsize=1)
def get_runtime() -> Runtime:
    return build_runtime(Settings.from_env())


def get_user_context(
    x_org_id: str | None = Header(default=None, alias="X-Org-ID"),
    x_site_id: str | None = Header(default=None, alias="X-Site-ID"),
    x_user_id: str | None = Header(default=None, alias="X-User-ID"),
) -> UserContext:
    if not all(value and value.strip() for value in (x_org_id, x_site_id, x_user_id)):
        raise HTTPException(status_code=401, detail="缺少可信的租户与用户上下文")
    return UserContext(org_id=x_org_id.strip(), site_id=x_site_id.strip(), user_id=x_user_id.strip())


def _response(result: JobResult) -> dict[str, Any]:
    return {
        "job_id": result.job_id,
        "status": result.status,
        "draft_id": result.draft_id,
        "error_code": result.error_code,
        "message": result.message,
        "lint_issues": result.lint_issues,
    }


app = FastAPI(title="Ops Generation Service", version="0.1.0")


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"error_code": None, "message": "请求参数无效，请检查 function_code 与 inputs"})


@app.get("/health", response_model=dict[str, str])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/generations", response_model=GenerationResponse, status_code=202)
def create_generation(
    request: GenerationRequest,
    user: UserContext = Depends(get_user_context),
    runtime: Runtime = Depends(get_runtime),
) -> dict[str, Any]:
    try:
        result = runtime.service.run(request.function_code, request.inputs, user)
    except RequestInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _response(result)


@app.get("/api/v1/generations/{job_id}", response_model=JobResponse, responses={404: {"model": ErrorResponse}})
def get_generation(
    job_id: str,
    user: UserContext = Depends(get_user_context),
    runtime: Runtime = Depends(get_runtime),
) -> dict[str, Any]:
    result = runtime.service.get_job(job_id, user)
    if result is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {
        **_response(result),
        "function_code": result.function_code,
        "input_sha256": result.input_sha256,
        "attempt_count": result.attempt_count,
    }


@app.post("/api/v1/generations/{job_id}/retry", response_model=GenerationResponse, status_code=202)
def retry_generation(
    job_id: str,
    user: UserContext = Depends(get_user_context),
    runtime: Runtime = Depends(get_runtime),
) -> dict[str, Any]:
    result = runtime.service.retry_queued(job_id, user)
    if result is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return _response(result)
