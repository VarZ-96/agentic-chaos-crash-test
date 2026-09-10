from __future__ import annotations

from typing import TYPE_CHECKING
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api import deps
from app.schemas.trace import TraceListResponse

if TYPE_CHECKING:
    from app.repositories.metrics_repo import MetricsRepository
    from app.repositories.trace_repo import TraceRepository

router = APIRouter(prefix="/traces", tags=["traces"])


@router.get(
    "",
    response_model=TraceListResponse,
)
@router.get(
    "/",
    response_model=TraceListResponse,
    include_in_schema=False,
)
async def list_traces(
    session_id: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    trace_repo: TraceRepository = Depends(deps.get_trace_repository),
) -> TraceListResponse:
    """Retrieve execution traces filtered by session and bounded by limit."""
    entries = await trace_repo.list_traces(session_id=session_id, limit=limit)
    count = await trace_repo.count_traces(session_id=session_id)
    return TraceListResponse(session_id=session_id, count=count, entries=entries)


@router.delete(
    "",
)
@router.delete(
    "/",
    include_in_schema=False,
)
async def purge_traces(
    session_id: str | None = None,
    trace_repo: TraceRepository = Depends(deps.get_trace_repository),
) -> dict[str, int]:
    """Purge execution traces globally or for a specific session."""
    purged_count = await trace_repo.purge(session_id=session_id)
    return {"purged": purged_count}


@router.get(
    "/runs/{session_id}",
)
async def get_run_metrics(
    session_id: str,
    metrics_repo: MetricsRepository = Depends(deps.get_metrics_repository),
) -> dict[str, object]:
    """Retrieve aggregated run metrics for a session."""
    run = await metrics_repo.get_run(session_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run '{session_id}' not found",
        )
    return run
