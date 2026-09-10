from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
from typing import Any
import pytest
import httpx

import main
from app.api import deps
from app.core.config import settings
from app.core.exceptions import ShortCircuitFault, UpstreamUnreachableError
from app.schemas.chaos_config import (
    ChaosConfig,
    EvaluationMetricsConfig,
    FuzzingProfilesConfig,
    NetworkChaosConfig,
    TargetAgentConfig,
)
from app.schemas.trace import TraceEntry
from app.services.chaos.base import BaseChaosStrategy
from app.services.proxy_service import ProxyService


# ============================================================================
# Test Doubles & Helper Factories
# ============================================================================

class FakeTraceRepo:
    """In-memory trace repository double recording save_trace invocations."""

    def __init__(self) -> None:
        self.saved_traces: list[TraceEntry] = []

    async def save_trace(self, entry: TraceEntry) -> int:
        self.saved_traces.append(entry)
        return len(self.saved_traces)


class FakeMetricsRepo:
    """In-memory metrics repository double recording record_run invocations."""

    def __init__(self) -> None:
        self.recorded_runs: list[dict[str, Any]] = []

    async def record_run(
        self,
        session_id: str,
        fault_count: int,
        total_faults_severity: float,
        short_circuits: int,
    ) -> None:
        self.recorded_runs.append({
            "session_id": session_id,
            "fault_count": fault_count,
            "total_faults_severity": total_faults_severity,
            "short_circuits": short_circuits,
        })


def _build_test_config(
    *,
    network_chaos: NetworkChaosConfig | None = None,
) -> ChaosConfig:
    return ChaosConfig(
        version="1.0",
        target_agent=TargetAgentConfig(
            base_proxy_url="http://localhost:8080/v1",
            max_allowed_token_budget=15000,
            upstream_base_url="http://upstream.test",
        ),
        fuzzing_profiles=FuzzingProfilesConfig(
            network_chaos=network_chaos,
        ),
        evaluation_metrics=EvaluationMetricsConfig(
            fail_on_infinite_loop=True,
            min_resiliency_score=0.82,
        ),
    )


class BuggyStrategy(BaseChaosStrategy):
    """Strategy raising an unexpected RuntimeError during request mutation."""

    name: str = "buggy_strategy"
    default_severity: float = 0.5

    def should_apply(self, request: httpx.Request, config: ChaosConfig) -> bool:
        return True

    async def mutate_request(self, request: httpx.Request, config: ChaosConfig) -> httpx.Request:
        raise RuntimeError("Unhandled internal glitch in chaos engine")

    async def mutate_response(self, response: httpx.Response, config: ChaosConfig) -> httpx.Response | None:
        return None


class ShortCircuitingStrategy(BaseChaosStrategy):
    """Strategy short-circuiting with a synthetic response."""

    name: str = "synthetic_short_circuit"
    default_severity: float = 0.9

    def should_apply(self, request: httpx.Request, config: ChaosConfig) -> bool:
        return True

    async def mutate_request(self, request: httpx.Request, config: ChaosConfig) -> httpx.Request:
        resp = httpx.Response(
            status_code=503,
            headers={"content-type": "application/json"},
            content=b'{"synthetic": true}',
        )
        raise ShortCircuitFault(response=resp, fault_name=self.name, severity=self.default_severity)

    async def mutate_response(self, response: httpx.Response, config: ChaosConfig) -> httpx.Response | None:
        return None


class ResponseMutatingStrategy(BaseChaosStrategy):
    """Strategy mutating responses when active."""

    name: str = "response_replacer"
    default_severity: float = 0.6

    def __init__(self, replace_body: bytes | None = None) -> None:
        self.replace_body = replace_body

    def should_apply(self, request: httpx.Request, config: ChaosConfig) -> bool:
        return True

    async def mutate_request(self, request: httpx.Request, config: ChaosConfig) -> httpx.Request:
        return request

    async def mutate_response(self, response: httpx.Response, config: ChaosConfig) -> httpx.Response | None:
        if self.replace_body is None:
            return None
        return httpx.Response(
            status_code=response.status_code,
            headers=dict(response.headers),
            content=self.replace_body,
        )


# ============================================================================
# Group 3a: ProxyService Unit Tests
# ============================================================================

@pytest.mark.asyncio
async def test_proxy_verbatim_forwarding():
    """ProxyService forwards method, path, query, and request body verbatim to upstream."""
    recorded_upstream_requests: list[httpx.Request] = []

    def upstream_handler(request: httpx.Request) -> httpx.Response:
        recorded_upstream_requests.append(request)
        return httpx.Response(
            status_code=201,
            headers={"x-upstream-ack": "yes"},
            content=b'{"created": 42}',
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream_handler)) as client:
        trace_repo = FakeTraceRepo()
        metrics_repo = FakeMetricsRepo()
        proxy = ProxyService(
            client=client,
            strategies=[],
            trace_repo=trace_repo,  # type: ignore[arg-type]
            metrics_repo=metrics_repo,  # type: ignore[arg-type]
        )

        sent_body = b'{"name": "test-payload", "nested": [1, 2, 3]}'
        status, headers, body = await proxy.forward(
            method="PUT",
            path="v2/resources/record-99",
            headers={"content-type": "application/json"},
            body=sent_body,
            query="action=sync&dry_run=false",
            session_id="session-forwarding-test",
            upstream_base_url="http://upstream.local:8080",
        )

    # 1. Verify upstream received exact request attributes
    assert len(recorded_upstream_requests) == 1
    upstream_req = recorded_upstream_requests[0]
    assert upstream_req.method == "PUT"
    assert upstream_req.url.path == "/v2/resources/record-99"
    assert upstream_req.url.query == b"action=sync&dry_run=false"
    assert upstream_req.content == sent_body

    # 2. Verify proxy returned exact upstream response attributes
    assert status == 201
    assert headers.get("x-upstream-ack") == "yes"
    assert body == b'{"created": 42}'

    # 3. Verify trace recorded verbatim values
    assert len(trace_repo.saved_traces) == 1
    trace = trace_repo.saved_traces[0]
    assert trace.request.method == "PUT"
    assert trace.request.path == "v2/resources/record-99"
    assert trace.response.status_code == 201
    assert trace.short_circuited is False


@pytest.mark.asyncio
async def test_proxy_header_filtering_strips_hop_by_hop_and_preserves_unrelated():
    """Hop-by-hop and X-Chaos-* headers are stripped in both directions while unrelated headers survive."""
    received_upstream_headers: dict[str, str] = {}

    def upstream_handler(request: httpx.Request) -> httpx.Response:
        received_upstream_headers.update(dict(request.headers))
        return httpx.Response(
            status_code=200,
            headers={
                "connection": "close",
                "transfer-encoding": "chunked",
                "x-chaos-upstream": "http://leak.test",
                "x-preserved-response-id": "resp-555",
            },
            content=b"ok",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream_handler)) as client:
        trace_repo = FakeTraceRepo()
        metrics_repo = FakeMetricsRepo()
        proxy = ProxyService(
            client=client,
            strategies=[],
            trace_repo=trace_repo,  # type: ignore[arg-type]
            metrics_repo=metrics_repo,  # type: ignore[arg-type]
        )

        inbound_headers = {
            "Host": "proxy.local:8000",
            "Connection": "keep-alive",
            "Content-Length": "123",
            "X-Chaos-Upstream": "http://override.upstream",
            "X-Chaos-Session": "session-strip-test",
            "Authorization": "Bearer secret-token",
            "X-App-Client-Trace": "client-trace-1234",
        }

        status, resp_headers, _ = await proxy.forward(
            method="GET",
            path="data",
            headers=inbound_headers,
            body=b"",
            query="",
            session_id="session-strip-test",
            upstream_base_url="http://upstream.local",
        )

    # 1. On request: hop-by-hop and x-chaos-* stripped before upstream.
    for stripped in ("connection", "content-length", "x-chaos-upstream", "x-chaos-session"):
        assert stripped not in received_upstream_headers

    # Host is NOT forwarded verbatim, but it must be REWRITTEN to the upstream authority.
    # Forwarding the client's `proxy.local:8000` would break virtual hosting; dropping it
    # entirely makes HTTP/1.1 origins reject the request outright.
    assert received_upstream_headers["host"] == "upstream.local"

    # Unrelated client headers preserved to upstream
    assert received_upstream_headers["authorization"] == "Bearer secret-token"
    assert received_upstream_headers["x-app-client-trace"] == "client-trace-1234"

    # 2. On response: hop-by-hop and x-chaos-* stripped from returned headers
    for stripped in ("connection", "transfer-encoding", "x-chaos-upstream"):
        assert stripped not in resp_headers

    # Unrelated response headers survive
    assert resp_headers["x-preserved-response-id"] == "resp-555"


@pytest.mark.asyncio
async def test_proxy_short_circuit_fault_aborts_upstream_and_records_trace():
    """ShortCircuitFault returns synthetic response; upstream call counter remains 0."""
    upstream_call_count = 0

    def upstream_handler(request: httpx.Request) -> httpx.Response:
        nonlocal upstream_call_count
        upstream_call_count += 1
        return httpx.Response(200, content=b"real upstream")

    strategy = ShortCircuitingStrategy()
    config = _build_test_config()

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream_handler)) as client:
        trace_repo = FakeTraceRepo()
        metrics_repo = FakeMetricsRepo()
        proxy = ProxyService(
            client=client,
            strategies=[strategy],
            trace_repo=trace_repo,  # type: ignore[arg-type]
            metrics_repo=metrics_repo,  # type: ignore[arg-type]
            config=config,
        )

        status, headers, body = await proxy.forward(
            method="POST",
            path="charge",
            headers={"content-type": "application/json"},
            body=b'{"amount": 100}',
            query="",
            session_id="session-short-circuit",
            upstream_base_url="http://upstream.local",
        )

    # Upstream was NEVER called
    assert upstream_call_count == 0

    # Synthetic response delivered to caller
    assert status == 503
    assert json.loads(body) == {"synthetic": True}

    # Trace persisted with short_circuited=True and fault logged
    assert len(trace_repo.saved_traces) == 1
    trace = trace_repo.saved_traces[0]
    assert trace.short_circuited is True
    assert len(trace.faults) == 1
    assert trace.faults[0].name == "synthetic_short_circuit"
    assert trace.faults[0].severity == 0.9

    # Metrics recorded run with short_circuits=1
    assert len(metrics_repo.recorded_runs) == 1
    run_metric = metrics_repo.recorded_runs[0]
    assert run_metric["short_circuits"] == 1
    assert run_metric["fault_count"] == 1


@pytest.mark.asyncio
async def test_proxy_mutate_response_none_vs_replacement():
    """mutate_response returning None leaves response byte-identical; returning Response replaces it."""
    original_bytes = b"\x00\x01\x02\xff\xfeRAW_BINARY_DATA"

    def upstream_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=original_bytes)

    config = _build_test_config()

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream_handler)) as client:
        trace_repo = FakeTraceRepo()
        metrics_repo = FakeMetricsRepo()

        # Case A: strategy returns None -> byte-identical output
        strategy_none = ResponseMutatingStrategy(replace_body=None)
        proxy_none = ProxyService(
            client=client,
            strategies=[strategy_none],
            trace_repo=trace_repo,  # type: ignore[arg-type]
            metrics_repo=metrics_repo,  # type: ignore[arg-type]
            config=config,
        )
        _, _, body_a = await proxy_none.forward(
            method="GET",
            path="binary",
            headers={},
            body=b"",
            query="",
            session_id="session-resp-none",
            upstream_base_url="http://upstream.local",
        )
        assert body_a == original_bytes

        # Case B: strategy returns new Response -> replacement body output
        replaced_bytes = b"COMPLETELY_REPLACED_BODY"
        strategy_replace = ResponseMutatingStrategy(replace_body=replaced_bytes)
        proxy_replace = ProxyService(
            client=client,
            strategies=[strategy_replace],
            trace_repo=trace_repo,  # type: ignore[arg-type]
            metrics_repo=metrics_repo,  # type: ignore[arg-type]
            config=config,
        )
        _, _, body_b = await proxy_replace.forward(
            method="GET",
            path="binary",
            headers={},
            body=b"",
            query="",
            session_id="session-resp-replace",
            upstream_base_url="http://upstream.local",
        )
        assert body_b == replaced_bytes


@pytest.mark.asyncio
async def test_proxy_strategy_runtime_error_skipped_request_succeeds():
    """When a strategy raises an unexpected RuntimeError, it is safely skipped and request completes."""
    def upstream_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"upstream reached successfully")

    config = _build_test_config()
    strategy = BuggyStrategy()

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream_handler)) as client:
        trace_repo = FakeTraceRepo()
        metrics_repo = FakeMetricsRepo()
        proxy = ProxyService(
            client=client,
            strategies=[strategy],
            trace_repo=trace_repo,  # type: ignore[arg-type]
            metrics_repo=metrics_repo,  # type: ignore[arg-type]
            config=config,
        )

        status, _, body = await proxy.forward(
            method="GET",
            path="safe",
            headers={},
            body=b"",
            query="",
            session_id="session-buggy-strategy",
            upstream_base_url="http://upstream.local",
        )

    assert status == 200
    assert body == b"upstream reached successfully"
    assert len(trace_repo.saved_traces) == 1
    assert trace_repo.saved_traces[0].response.status_code == 200


@pytest.mark.asyncio
async def test_proxy_connect_error_raises_upstream_unreachable_and_persists_trace():
    """httpx.ConnectError maps to UpstreamUnreachableError and persists an error trace."""
    def upstream_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused by peer", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream_handler)) as client:
        trace_repo = FakeTraceRepo()
        metrics_repo = FakeMetricsRepo()
        proxy = ProxyService(
            client=client,
            strategies=[],
            trace_repo=trace_repo,  # type: ignore[arg-type]
            metrics_repo=metrics_repo,  # type: ignore[arg-type]
        )

        with pytest.raises(UpstreamUnreachableError) as exc_info:
            await proxy.forward(
                method="POST",
                path="service/action",
                headers={"x-call": "test"},
                body=b"payload",
                query="param=1",
                session_id="session-unreachable",
                upstream_base_url="http://192.0.2.1:9999",
            )

    assert "http://192.0.2.1:9999/service/action?param=1" in exc_info.value.url
    assert "Connection refused" in exc_info.value.reason

    # A trace entry MUST still be persisted with status_code=0
    assert len(trace_repo.saved_traces) == 1
    trace = trace_repo.saved_traces[0]
    assert trace.session_id == "session-unreachable"
    assert trace.response.status_code == 0
    assert trace.request.upstream_url == "http://192.0.2.1:9999/service/action?param=1"


@pytest.mark.asyncio
async def test_proxy_step_index_increments_independently_per_session():
    """step_index increments monotonically per session and is independent across sessions."""
    def upstream_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"ok")

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream_handler)) as client:
        trace_repo = FakeTraceRepo()
        metrics_repo = FakeMetricsRepo()
        proxy = ProxyService(
            client=client,
            strategies=[],
            trace_repo=trace_repo,  # type: ignore[arg-type]
            metrics_repo=metrics_repo,  # type: ignore[arg-type]
        )

        async def call(session_id: str) -> None:
            await proxy.forward(
                method="GET",
                path="step",
                headers={},
                body=b"",
                query="",
                session_id=session_id,
                upstream_base_url="http://upstream.local",
            )

        # Interleave calls for Session A and Session B
        await call("sess_A")  # A: 0
        await call("sess_B")  # B: 0
        await call("sess_A")  # A: 1
        await call("sess_A")  # A: 2
        await call("sess_B")  # B: 1
        await call("sess_A")  # A: 3

    traces_a = [t for t in trace_repo.saved_traces if t.session_id == "sess_A"]
    traces_b = [t for t in trace_repo.saved_traces if t.session_id == "sess_B"]

    assert [t.step_index for t in traces_a] == [0, 1, 2, 3]
    assert [t.step_index for t in traces_b] == [0, 1]


@pytest.mark.asyncio
async def test_proxy_none_config_pure_proxies_with_zero_mutation():
    """When config=None and no instance config is set, proxy operates with zero mutation."""
    def upstream_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"pure upstream response")

    # Strategy would short-circuit if evaluated
    active_strategy = ShortCircuitingStrategy()

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream_handler)) as client:
        trace_repo = FakeTraceRepo()
        metrics_repo = FakeMetricsRepo()
        proxy = ProxyService(
            client=client,
            strategies=[active_strategy],
            trace_repo=trace_repo,  # type: ignore[arg-type]
            metrics_repo=metrics_repo,  # type: ignore[arg-type]
            config=None,  # No instance config
        )

        status, _, body = await proxy.forward(
            method="GET",
            path="data",
            headers={},
            body=b"",
            query="",
            session_id="session-pure-proxy",
            upstream_base_url="http://upstream.local",
            config=None,  # Explicitly None per-call override
        )

    # Must NOT short-circuit; returns upstream response directly
    assert status == 200
    assert body == b"pure upstream response"
    assert len(trace_repo.saved_traces) == 1
    assert trace_repo.saved_traces[0].short_circuited is False
    assert len(trace_repo.saved_traces[0].faults) == 0


# ============================================================================
# Group 3b: API-Level Tests against Real main.app
# ============================================================================

@pytest.fixture
async def api_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolated test client bound to main.app with temporary SQLite database and stubbed upstream."""
    test_db = tmp_path / "test_api_traces.db"
    monkeypatch.setattr(settings, "SQLITE_PATH", str(test_db))
    monkeypatch.setattr(settings, "UPSTREAM_BASE_URL", "http://upstream.local")

    def upstream_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            headers={"content-type": "application/json"},
            content=json.dumps({"proxied_path": request.url.path}).encode("utf-8"),
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream_handler))

    async with main.app.router.lifespan_context(main.app):
        proxy = deps.get_proxy_service()
        orig_client = proxy._client
        proxy._client = mock_client
        try:
            transport = httpx.ASGITransport(app=main.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                yield client
        finally:
            proxy._client = orig_client
            await mock_client.aclose()


@pytest.mark.asyncio
async def test_api_health_endpoint_reports_status_and_strategies(api_client: httpx.AsyncClient):
    """GET /health reports status ok, chaos_config_loaded, and strategy list."""
    resp = await api_client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["chaos_config_loaded"] is True
    assert isinstance(data["strategies"], list)
    assert "network_chaos" in data["strategies"]


@pytest.mark.asyncio
async def test_api_route_ordering_traces_and_chaos_not_swallowed_by_catch_all(
    api_client: httpx.AsyncClient,
):
    """REGRESSION TEST: GET /v1/traces and GET /v1/chaos/config are handled by their dedicated routers,
    not swallowed by the /v1/{path:path} catch-all proxy router.
    """
    # 1. Traces endpoint must return TraceListResponse DTO
    traces_resp = await api_client.get("/v1/traces")
    assert traces_resp.status_code == 200
    traces_data = traces_resp.json()
    assert "entries" in traces_data
    assert "count" in traces_data
    assert "session_id" in traces_data
    assert "proxied_path" not in traces_data

    # 2. Chaos config endpoint must return ChaosConfig model
    config_resp = await api_client.get("/v1/chaos/config")
    assert config_resp.status_code == 200
    config_data = config_resp.json()
    assert "version" in config_data
    assert "target_agent" in config_data
    assert "fuzzing_profiles" in config_data
    assert "proxied_path" not in config_data


@pytest.mark.asyncio
async def test_api_traces_limit_validation_zero_and_exceeded_yield_422(
    api_client: httpx.AsyncClient,
):
    """limit=0 and limit=5000 fail FastAPI Query validation (ge=1, le=1000) with 422."""
    resp_zero = await api_client.get("/v1/traces?limit=0")
    assert resp_zero.status_code == 422

    resp_excess = await api_client.get("/v1/traces?limit=5000")
    assert resp_excess.status_code == 422

    resp_valid = await api_client.get("/v1/traces?limit=50")
    assert resp_valid.status_code == 200


@pytest.mark.asyncio
async def test_api_proxy_echoes_and_honors_session_header(api_client: httpx.AsyncClient):
    """Proxy echoes X-Chaos-Session: honors client-provided session id or generates one."""
    custom_session = "custom-agent-session-789"
    resp_custom = await api_client.get(
        "/v1/agent/task",
        headers={"x-chaos-session": custom_session},
    )
    assert resp_custom.headers.get("x-chaos-session") == custom_session

    # Without session header, a generated 32-char hex session id is returned
    resp_auto = await api_client.get("/v1/agent/task")
    auto_session = resp_auto.headers.get("x-chaos-session")
    assert auto_session is not None
    assert len(auto_session) == 32


@pytest.mark.asyncio
async def test_api_chaos_config_hot_swap(api_client: httpx.AsyncClient):
    """PUT /v1/chaos/config hot-swaps in-memory config and GET /v1/chaos/config reflects it."""
    get_orig = await api_client.get("/v1/chaos/config")
    assert get_orig.status_code == 200
    orig_cfg = get_orig.json()

    # Modify a specific field
    modified_cfg = dict(orig_cfg)
    modified_cfg["target_agent"] = dict(orig_cfg["target_agent"])
    modified_cfg["target_agent"]["max_allowed_token_budget"] = 88888

    put_resp = await api_client.put("/v1/chaos/config", json=modified_cfg)
    assert put_resp.status_code == 200
    assert put_resp.json()["target_agent"]["max_allowed_token_budget"] == 88888

    # Subsequent GET reflects updated config
    get_updated = await api_client.get("/v1/chaos/config")
    assert get_updated.status_code == 200
    assert get_updated.json()["target_agent"]["max_allowed_token_budget"] == 88888


@pytest.mark.asyncio
async def test_api_traces_unknown_run_returns_404(api_client: httpx.AsyncClient):
    """GET /v1/traces/runs/<unknown> returns 404 with run not found error."""
    resp = await api_client.get("/v1/traces/runs/completely-unknown-session-id")
    assert resp.status_code == 404
    data = resp.json()
    assert "Run 'completely-unknown-session-id' not found" in data["detail"]


# ============================================================================
# Group 3c: Regression Tests for Defect Fixes
# ============================================================================

@pytest.mark.asyncio
async def test_proxy_hot_swap_behavioural_takes_effect():
    """Regression test (Defect 4): set_config must dynamically rebuild strategies so hot-swapping
    actually alters runtime forwarding behaviour (enabling token_trap short-circuits without
    calling upstream, and subsequent disabling forwards to upstream unmodified).
    """
    from app.schemas.chaos_config import TokenTrapConfig

    upstream_calls = 0

    def upstream_handler(request: httpx.Request) -> httpx.Response:
        nonlocal upstream_calls
        upstream_calls += 1
        return httpx.Response(200, json={"status": "upstream_ok"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(upstream_handler))
    trace_repo = FakeTraceRepo()
    metrics_repo = FakeMetricsRepo()

    # Construct ProxyService starting with empty strategy list
    proxy = ProxyService(
        client=client,
        strategies=[],
        trace_repo=trace_repo,
        metrics_repo=metrics_repo,
        config=None,
    )

    # Hot-swap config with token_trap enabled
    cfg_with_trap = ChaosConfig(
        version="1.0",
        target_agent=TargetAgentConfig(
            base_proxy_url="http://localhost:8080/v1",
            max_allowed_token_budget=1000,
            upstream_base_url="http://upstream.test",
        ),
        fuzzing_profiles=FuzzingProfilesConfig(
            token_trap=TokenTrapConfig(
                enabled=True,
                max_cyclic_depth=3,
                trap_paths=["/api/loop_trap"],
                redirect_message="Redirecting to {next_path}",
            )
        ),
        evaluation_metrics=EvaluationMetricsConfig(
            fail_on_infinite_loop=True,
            min_resiliency_score=0.82,
        ),
    )
    proxy.set_config(cfg_with_trap)

    # Forward to trap path: must short-circuit, upstream must NOT be called
    status_1, headers_1, body_1 = await proxy.forward(
        method="GET",
        path="/api/loop_trap",
        headers={},
        body=b"",
        query="",
        session_id="session-hotswap",
        upstream_base_url="http://upstream.test",
    )

    assert status_1 == 200
    assert upstream_calls == 0, f"Upstream called {upstream_calls} times; expected 0 due to token_trap short-circuit"
    data_1 = json.loads(body_1)
    assert data_1.get("injected_by") == "token_trap"

    # Hot-swap config with all profiles disabled
    cfg_disabled = ChaosConfig(
        version="1.0",
        target_agent=TargetAgentConfig(
            base_proxy_url="http://localhost:8080/v1",
            max_allowed_token_budget=1000,
            upstream_base_url="http://upstream.test",
        ),
        fuzzing_profiles=FuzzingProfilesConfig(),
        evaluation_metrics=EvaluationMetricsConfig(
            fail_on_infinite_loop=True,
            min_resiliency_score=0.82,
        ),
    )
    proxy.set_config(cfg_disabled)

    # Subsequent forward to the same path: reaches upstream unmodified
    status_2, headers_2, body_2 = await proxy.forward(
        method="GET",
        path="/api/loop_trap",
        headers={},
        body=b"",
        query="",
        session_id="session-hotswap",
        upstream_base_url="http://upstream.test",
    )

    assert status_2 == 200
    assert upstream_calls == 1, f"Upstream called {upstream_calls} times; expected 1 after disabling token_trap"
    data_2 = json.loads(body_2)
    assert data_2 == {"status": "upstream_ok"}


@pytest.mark.asyncio
async def test_proxy_credential_redaction_in_persisted_traces():
    """Regression test (Defect 5): sensitive request headers (Authorization, Cookie, X-Api-Key)
    and response headers (Set-Cookie) must be redacted to '[REDACTED]' in persisted traces
    without redacting the real values forwarded upstream or returned to the caller.
    Also covers UpstreamUnreachableError error-logging path.
    """
    upstream_received_headers: dict[str, str] = {}

    def secure_handler(request: httpx.Request) -> httpx.Response:
        nonlocal upstream_received_headers
        upstream_received_headers = dict(request.headers)
        return httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "set-cookie": "session_token=secret_val_xyz; Secure",
                "x-app-response": "ok",
            },
            json={"status": "authenticated"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(secure_handler))
    trace_repo = FakeTraceRepo()
    metrics_repo = FakeMetricsRepo()
    proxy = ProxyService(
        client=client,
        strategies=[],
        trace_repo=trace_repo,
        metrics_repo=metrics_repo,
        config=None,
    )

    request_headers = {
        "Authorization": "Bearer supersecret",
        "Cookie": "a=b",
        "X-Api-Key": "k-123",
        "X-App-Trace": "keep",
    }

    caller_status, caller_headers, caller_body = await proxy.forward(
        method="POST",
        path="/secure/data",
        headers=request_headers,
        body=b'{"action":"fetch"}',
        query="",
        session_id="session-redact-1",
        upstream_base_url="http://upstream.test",
    )

    # (a) UPSTREAM received the REAL Authorization value and other auth headers
    assert upstream_received_headers.get("authorization") == "Bearer supersecret"
    assert upstream_received_headers.get("cookie") == "a=b"
    assert upstream_received_headers.get("x-api-key") == "k-123"
    assert upstream_received_headers.get("x-app-trace") == "keep"

    # (b) PERSISTED TraceEntry.request.headers has each sensitive header's value as exactly "[REDACTED]"
    assert len(trace_repo.saved_traces) == 1
    persisted_req_headers = trace_repo.saved_traces[0].request.headers
    assert persisted_req_headers["authorization"] == "[REDACTED]"
    assert persisted_req_headers["cookie"] == "[REDACTED]"
    assert persisted_req_headers["x-api-key"] == "[REDACTED]"
    assert persisted_req_headers["x-app-trace"] == "keep"

    # (c) set-cookie response header: redacted in persisted ResponseLog, NOT in caller headers
    persisted_resp_headers = trace_repo.saved_traces[0].response.headers
    assert persisted_resp_headers["set-cookie"] == "[REDACTED]"
    assert caller_headers["set-cookie"] == "session_token=secret_val_xyz; Secure"

    # (d) UpstreamUnreachableError path: redaction must hold in persisted trace too
    def unreachable_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused by upstream")

    unreach_client = httpx.AsyncClient(transport=httpx.MockTransport(unreachable_handler))
    unreach_proxy = ProxyService(
        client=unreach_client,
        strategies=[],
        trace_repo=trace_repo,
        metrics_repo=metrics_repo,
        config=None,
    )

    with pytest.raises(UpstreamUnreachableError):
        await unreach_proxy.forward(
            method="POST",
            path="/unreachable/endpoint",
            headers=request_headers,
            body=b"unreachable-payload",
            query="",
            session_id="session-redact-unreach",
            upstream_base_url="http://unreachable.test",
        )

    assert len(trace_repo.saved_traces) == 2
    unreach_trace = trace_repo.saved_traces[1]
    assert unreach_trace.request.headers["authorization"] == "[REDACTED]"
    assert unreach_trace.request.headers["cookie"] == "[REDACTED]"
    assert unreach_trace.request.headers["x-api-key"] == "[REDACTED]"
    assert unreach_trace.request.headers["x-app-trace"] == "keep"


@pytest.mark.asyncio
async def test_proxy_step_indices_memory_bound_and_monotonic():
    """Regression test (Defect 6): ProxyService._step_indices must be bounded at 10_000 entries
    via FIFO eviction across distinct sessions, and a still-tracked session keeps
    incrementing its step index monotonically.
    """
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    trace_repo = FakeTraceRepo()
    metrics_repo = FakeMetricsRepo()
    proxy = ProxyService(
        client=client,
        strategies=[],
        trace_repo=trace_repo,
        metrics_repo=metrics_repo,
        config=None,
    )

    # Issue forward() across 10_100 distinct session ids
    for i in range(10_100):
        await proxy.forward(
            method="GET",
            path="/ping",
            headers={},
            body=b"",
            query="",
            session_id=f"session-bound-{i}",
            upstream_base_url="http://upstream.test",
        )

    # Memory bound invariant
    assert len(proxy._step_indices) <= 10_000, (
        f"Memory leak in _step_indices: expected <= 10000 entries, got {len(proxy._step_indices)}"
    )

    # A still-tracked session (session-bound-10099) continues to increment monotonically
    tracked_session = "session-bound-10099"
    assert proxy._step_indices[tracked_session] == 1

    await proxy.forward(
        method="GET",
        path="/ping",
        headers={},
        body=b"",
        query="",
        session_id=tracked_session,
        upstream_base_url="http://upstream.test",
    )
    assert proxy._step_indices[tracked_session] == 2

    # Persisted trace step_index for that call is 1 (monotonic: 0 on first call, 1 on second)
    last_trace = trace_repo.saved_traces[-1]
    assert last_trace.session_id == tracked_session
    assert last_trace.step_index == 1


@pytest.mark.asyncio
async def test_metrics_repository_no_io_on_hot_path(tmp_path: Path):
    """Regression test (Defect 7): MetricsRepository.record_run accumulates strictly in-memory
    with zero database I/O on the hot path. Direct SQLite inspection confirms 0 rows
    until flush(); flush() persists additive sums; get_run() is self-consistent
    without requiring an explicit caller flush.
    """
    import aiosqlite
    from app.repositories.metrics_repo import MetricsRepository

    db_file = tmp_path / "metrics_test.db"
    repo = MetricsRepository(sqlite_path=str(db_file))
    await repo.initialize()

    try:
        # Record multiple runs for the same session
        await repo.record_run(
            session_id="session-metrics-hot",
            fault_count=2,
            total_faults_severity=1.5,
            short_circuits=1,
        )
        await repo.record_run(
            session_id="session-metrics-hot",
            fault_count=3,
            total_faults_severity=2.0,
            short_circuits=0,
        )

        # Hot path verification: table must be EMPTY when inspected directly via raw SQLite
        async with aiosqlite.connect(str(db_file)) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM execution_runs")
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == 0, f"Expected 0 rows in execution_runs before flush, found {row[0]}"

        # Flush accumulated in-memory deltas to disk
        await repo.flush()

        # Verify persisted totals match additive sums
        async with aiosqlite.connect(str(db_file)) as db:
            cursor = await db.execute(
                "SELECT fault_count, total_faults_severity, short_circuits FROM execution_runs WHERE session_id = 'session-metrics-hot'"
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == 5
            assert abs(row[1] - 3.5) < 1e-6
            assert row[2] == 1

        # Record further metrics without calling flush() explicitly
        await repo.record_run(
            session_id="session-metrics-hot",
            fault_count=1,
            total_faults_severity=0.5,
            short_circuits=1,
        )

        # get_run() must be self-consistent without an explicit caller flush
        run_data = await repo.get_run("session-metrics-hot")
        assert run_data is not None
        assert run_data["fault_count"] == 6
        assert abs(run_data["total_faults_severity"] - 4.0) < 1e-6
        assert run_data["short_circuits"] == 2
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_trace_repository_save_trace_returns_none_and_persists_after_flush(tmp_path: Path):
    """Regression test (Defect 8): TraceRepository.save_trace is non-blocking fire-and-forget,
    returning None rather than a synchronous row ID, and persists to the database
    once flush() is awaited.
    """
    from app.repositories.trace_repo import TraceRepository
    from app.schemas.trace import RequestLog, ResponseLog

    db_file = tmp_path / "trace_test.db"
    repo = TraceRepository(sqlite_path=str(db_file))
    await repo.initialize()

    try:
        entry = TraceEntry(
            session_id="session-trace-contract",
            step_index=0,
            request=RequestLog(
                method="POST",
                path="/order",
                upstream_url="http://upstream.test/order",
                headers={"authorization": "[REDACTED]"},
                body_sha256=None,
                body_preview=None,
            ),
            response=ResponseLog(
                status_code=201,
                headers={"content-type": "application/json"},
                body_preview='{"order_id": 99}',
                duration_ms=8.5,
            ),
            faults=[],
            short_circuited=False,
        )

        # Pin contract: save_trace returns None (not int / row id)
        res = await repo.save_trace(entry)
        assert res is None, f"Expected save_trace to return None, got {res!r}"

        # Flush queue and verify trace is persisted
        await repo.flush()

        traces = await repo.list_traces("session-trace-contract")
        assert len(traces) == 1
        assert traces[0].session_id == "session-trace-contract"
        assert traces[0].request.path == "/order"
        assert traces[0].response.status_code == 201
        assert traces[0].id is not None
    finally:
        await repo.close()
