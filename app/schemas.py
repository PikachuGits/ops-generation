from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class GenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    function_code: str = Field(min_length=1, max_length=64)
    inputs: dict[str, Any]

    @field_validator("function_code")
    @classmethod
    def normalize_function_code(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("function_code 不能为空")
        return value

    @field_validator("inputs")
    @classmethod
    def require_inputs(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not value:
            raise ValueError("inputs 不能为空")
        return value


class GenerationResponse(BaseModel):
    job_id: str
    status: Literal["queued", "drafted", "failed"]
    draft_id: str | None = None
    error_code: str | None = None
    message: str | None = None
    lint_issues: list[dict[str, Any]] = Field(default_factory=list)


class JobResponse(GenerationResponse):
    function_code: str
    input_sha256: str
    attempt_count: int


class ErrorResponse(BaseModel):
    error_code: str | None = None
    message: str
