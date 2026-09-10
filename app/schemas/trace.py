from __future__ import annotations

from datetime import datetime, timezone
from pydantic import BaseModel, ConfigDict, Field


class RequestLog(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    method: str
    path: str
    upstream_url: str
    headers: dict[str, str] = Field(default_factory=dict)
    body_sha256: str | None = None
    body_preview: str | None = None


class ResponseLog(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status_code: int
    headers: dict[str, str] = Field(default_factory=dict)
    body_preview: str | None = None
    duration_ms: float = 0.0


class FaultLog(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    severity: float
    stage: str
    detail: str


class TraceEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    session_id: str
    step_index: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    request: RequestLog
    response: ResponseLog
    faults: list[FaultLog] = Field(default_factory=list)
    short_circuited: bool = False


class TraceListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    session_id: str | None = None
    count: int
    entries: list[TraceEntry] = Field(default_factory=list)
