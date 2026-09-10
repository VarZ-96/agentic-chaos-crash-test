from __future__ import annotations

import uuid
from typing import TYPE_CHECKING
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.api import deps
from app.core.config import settings
from app.core.exceptions import UpstreamResolutionError, UpstreamUnreachableError

if TYPE_CHECKING:
    from app.schemas.chaos_config import ChaosConfig
    from app.services.proxy_service import ProxyService

router = APIRouter(tags=["proxy"])


@router.api_route(
    "",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    include_in_schema=False,
)
@router.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
async def proxy_endpoint(
    request: Request,
    path: str = "",
    proxy_service: ProxyService = Depends(deps.get_proxy_service),
    chaos_config: ChaosConfig | None = Depends(deps.get_chaos_config_optional),
) -> Response:
    """Catch-all reverse proxy endpoint forwarding requests with injected chaos."""
    body = await request.body()
    query = request.url.query

    session_header = request.headers.get("x-chaos-session")
    if session_header and session_header.strip():
        session_id = session_header.strip()
    else:
        session_id = uuid.uuid4().hex

    upstream_override = request.headers.get("x-chaos-upstream")
    try:
        upstream_base_url = settings.resolve_upstream(upstream_override)
    except UpstreamResolutionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    try:
        status_code, resp_headers, resp_body = await proxy_service.forward(
            method=request.method,
            path=path,
            headers=dict(request.headers),
            body=body,
            query=query,
            session_id=session_id,
            upstream_base_url=upstream_base_url,
            config=chaos_config,
        )
    except UpstreamResolutionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except UpstreamUnreachableError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    final_headers = {k: v for k, v in resp_headers.items() if k.lower() != "x-chaos-session"}
    final_headers["X-Chaos-Session"] = session_id

    return Response(
        content=resp_body,
        status_code=status_code,
        headers=final_headers,
    )
