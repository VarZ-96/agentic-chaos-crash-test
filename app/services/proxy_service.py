from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

import httpx

from app.core.exceptions import ShortCircuitFault, UpstreamUnreachableError
from app.models.state import InterceptContext
from app.schemas.chaos_config import ChaosConfig
from app.schemas.trace import FaultLog, RequestLog, ResponseLog, TraceEntry
from app.services.chaos import build_strategies
from app.services.chaos.base import BaseChaosStrategy
from app.utils.hashing import hash_body
from app.utils.logger import get_logger

if TYPE_CHECKING:
    from app.repositories.metrics_repo import MetricsRepository
    from app.repositories.trace_repo import TraceRepository

logger = get_logger("proxy_service")

# Hop-by-hop and chaos control headers to strip case-insensitively on both request and response.
_MAX_TRACKED_SESSIONS: int = 10_000

HOP_BY_HOP_HEADERS: frozenset[str] = frozenset(
    {
        "host",
        "content-length",
        "connection",
        "keep-alive",
        "transfer-encoding",
        "upgrade",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "x-chaos-upstream",
        "x-chaos-session",
    }
)


def _filter_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Filter out hop-by-hop and chaos control headers case-insensitively."""
    return {
        k: v
        for k, v in headers.items()
        if k.lower() not in HOP_BY_HOP_HEADERS
    }

# Sensitive header names whose values must be redacted when persisting trace logs.
SENSITIVE_HEADERS: frozenset[str] = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "api-key",
        "x-auth-token",
    }
)


def _redact_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Return a copy of headers with values of sensitive header names replaced by [REDACTED]."""
    return {
        k: ("[REDACTED]" if k.lower() in SENSITIVE_HEADERS else v)
        for k, v in headers.items()
    }


class ProxyService:
    """Asynchronous reverse proxy forwarding service with chaos fault injection."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        strategies: Sequence[BaseChaosStrategy],
        trace_repo: TraceRepository,
        metrics_repo: MetricsRepository,
        config: ChaosConfig | None = None,
    ) -> None:
        self._client = client
        self._strategies = list(strategies)
        self._trace_repo = trace_repo
        self._metrics_repo = metrics_repo
        self._config = config
        self._step_indices: dict[str, int] = {}
        self._step_lock = asyncio.Lock()

    def set_config(self, config: ChaosConfig | None) -> None:
        """Update active chaos configuration for the proxy service and rebuild strategies."""
        self._config = config
        self._strategies = build_strategies(config)

    async def forward(
        self,
        *,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes,
        query: str,
        session_id: str,
        upstream_base_url: str,
        config: ChaosConfig | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        """Forward an intercepted HTTP request through the chaos mutation pipeline."""
        raw_body = body
        raw_headers = headers
        raw_query = query

        # Per-session monotonic step index increment guarded by lock
        async with self._step_lock:
            step_index = self._step_indices.get(session_id, 0)
            self._step_indices[session_id] = step_index + 1
            while len(self._step_indices) > _MAX_TRACKED_SESSIONS:
                oldest_key = next(iter(self._step_indices))
                del self._step_indices[oldest_key]

        # Build normalized target URL
        base_url = upstream_base_url.rstrip("/")
        normalized_path = "/" + path.lstrip("/")
        target_url = f"{base_url}{normalized_path}"
        if raw_query:
            target_url = f"{target_url}?{raw_query}"

        # Initialize intercept context
        ctx = InterceptContext(
            session_id=session_id,
            method=method,
            path=path,
            upstream_url=target_url,
            step_index=step_index,
        )

        # Strip hop-by-hop/control headers from the INBOUND set only. httpx then derives the
        # correct Host for the upstream URL; popping them off the constructed request would
        # delete that generated Host and the upstream would reject the request.
        filtered_req_headers = _filter_headers(raw_headers)
        req = httpx.Request(method, target_url, headers=filtered_req_headers, content=raw_body)

        # Resolve active chaos configuration: per-call override wins over the instance default.
        # Note: forward()'s per-call config override only affects should_apply gating, not the
        # instance strategy list; that asymmetry is acceptable because the admin API always goes
        # through set_config.
        cfg = config if config is not None else self._config

        # REQUEST CHAIN
        resp: httpx.Response | None = None
        if cfg is not None:
            try:
                for s in self._strategies:
                    try:
                        if s.should_apply(req, cfg):
                            mutated = await s.mutate_request(req, cfg)
                            # Only a real change is a fault. `should_apply` merely means the
                            # profile is armed; recording unconditionally would inflate fault
                            # counts and skew the Phase 3 resiliency score.
                            if mutated is not req:
                                req = mutated
                                ctx.record_fault(
                                    s.name,
                                    s.default_severity,
                                    "request",
                                    f"{s.name} request mutation applied",
                                )
                    except ShortCircuitFault:
                        raise
                    except Exception as exc:
                        logger.error(
                            "Chaos strategy %s failed during request mutation: %s",
                            s.name,
                            exc,
                            exc_info=True,
                        )
            except ShortCircuitFault as exc:
                ctx.short_circuited = True
                ctx.record_fault(exc.fault_name, exc.severity, "request", str(exc))
                resp = exc.response

        # NOTE: no re-strip of hop-by-hop headers here. The inbound filter already removed
        # them, and popping `host` off the built request deletes the Host httpx derived for
        # the upstream URL, which HTTP/1.1 origins reject outright.

        # UPSTREAM DISPATCH
        if not ctx.short_circuited:
            try:
                resp = await self._client.send(req)
            except (httpx.TimeoutException, httpx.RequestError) as exc:
                req_content = getattr(req, "content", None)
                if req_content is None:
                    req_content = raw_body
                req_body_preview = req_content.decode("utf-8", errors="replace")[:2048] if req_content else None
                req_body_sha256 = hash_body(req_content) if req_content else None

                req_log = RequestLog(
                    method=req.method,
                    path=path,
                    upstream_url=target_url,
                    headers=_redact_headers(dict(req.headers)),
                    body_sha256=req_body_sha256,
                    body_preview=req_body_preview,
                )
                resp_log = ResponseLog(
                    status_code=0,
                    headers={},
                    body_preview=None,
                    duration_ms=ctx.elapsed_ms(),
                )
                await self._persist_trace_and_metrics(
                    session_id=session_id,
                    ctx=ctx,
                    request_log=req_log,
                    response_log=resp_log,
                )
                raise UpstreamUnreachableError(target_url, str(exc)) from exc

        # RESPONSE CHAIN (skipped if short-circuited)
        if not ctx.short_circuited and cfg is not None and resp is not None:
            for s in self._strategies:
                try:
                    if s.should_apply(req, cfg):
                        out = await s.mutate_response(resp, cfg)
                        if out is not None:
                            resp = out
                            severity = s.default_severity
                            ctx.record_fault(
                                s.name,
                                severity,
                                "response",
                                f"{s.name} response mutation applied",
                            )
                except Exception as exc:
                    logger.error(
                        "Chaos strategy %s failed during response mutation: %s",
                        s.name,
                        exc,
                        exc_info=True,
                    )

        assert resp is not None, "Response must not be None after dispatch or short-circuit"

        # Read response content and strip hop-by-hop headers
        resp_bytes = await resp.aread() if hasattr(resp, "aread") else resp.content
        filtered_resp_headers = _filter_headers(resp.headers)

        # Build Trace logs
        req_content = getattr(req, "content", None)
        if req_content is None:
            req_content = raw_body
        req_body_preview = req_content.decode("utf-8", errors="replace")[:2048] if req_content else None
        req_body_sha256 = hash_body(req_content) if req_content else None

        req_log = RequestLog(
            method=req.method,
            path=path,
            upstream_url=target_url,
            headers=_redact_headers(dict(req.headers)),
            body_sha256=req_body_sha256,
            body_preview=req_body_preview,
        )

        resp_body_preview = resp_bytes.decode("utf-8", errors="replace")[:2048] if resp_bytes else None
        resp_log = ResponseLog(
            status_code=resp.status_code,
            headers=_redact_headers(filtered_resp_headers),
            body_preview=resp_body_preview,
            duration_ms=ctx.elapsed_ms(),
        )

        await self._persist_trace_and_metrics(
            session_id=session_id,
            ctx=ctx,
            request_log=req_log,
            response_log=resp_log,
        )

        return resp.status_code, filtered_resp_headers, resp_bytes

    async def _persist_trace_and_metrics(
        self,
        *,
        session_id: str,
        ctx: InterceptContext,
        request_log: RequestLog,
        response_log: ResponseLog,
    ) -> None:
        fault_logs = [
            FaultLog(
                name=f.name,
                severity=f.severity,
                stage=f.stage,
                detail=f.detail,
            )
            for f in ctx.faults
        ]
        entry = TraceEntry(
            session_id=session_id,
            step_index=ctx.step_index,
            request=request_log,
            response=response_log,
            faults=fault_logs,
            short_circuited=ctx.short_circuited,
        )
        try:
            await self._trace_repo.save_trace(entry)
            fault_count = len(ctx.faults)
            total_severity = sum(f.severity for f in ctx.faults)
            short_circuits = 1 if ctx.short_circuited else 0
            await self._metrics_repo.record_run(
                session_id=session_id,
                fault_count=fault_count,
                total_faults_severity=total_severity,
                short_circuits=short_circuits,
            )
        except Exception as exc:
            logger.error("Failed to persist trace or metrics: %s", exc, exc_info=True)
