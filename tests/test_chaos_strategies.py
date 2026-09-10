from __future__ import annotations

from datetime import datetime, timezone
import json
import random
import time
import pytest
import httpx

from app.core.exceptions import ShortCircuitFault
from app.schemas.chaos_config import (
    ChaosConfig,
    EvaluationMetricsConfig,
    FuzzingProfilesConfig,
    NetworkChaosConfig,
    RbacJailbreakerConfig,
    SchemaMutilationConfig,
    SemanticMirageConfig,
    TargetAgentConfig,
    TokenTrapConfig,
)
from app.services.chaos import (
    NetworkChaosStrategy,
    RbacJailbreakStrategy,
    SchemaMutilationStrategy,
    SemanticMirageStrategy,
    TokenTrapStrategy,
    build_strategies,
)


def _make_config(
    *,
    network_chaos: NetworkChaosConfig | None = None,
    schema_mutilation: SchemaMutilationConfig | None = None,
    semantic_mirage: SemanticMirageConfig | None = None,
    rbac_jailbreaker: RbacJailbreakerConfig | None = None,
    token_trap: TokenTrapConfig | None = None,
) -> ChaosConfig:
    """Helper factory constructing valid ChaosConfig instances for unit tests."""
    return ChaosConfig(
        version="1.0",
        target_agent=TargetAgentConfig(
            base_proxy_url="http://localhost:8080/v1",
            max_allowed_token_budget=15000,
            upstream_base_url="http://127.0.0.1:9009",
        ),
        fuzzing_profiles=FuzzingProfilesConfig(
            network_chaos=network_chaos,
            schema_mutilation=schema_mutilation,
            semantic_mirage=semantic_mirage,
            rbac_jailbreaker=rbac_jailbreaker,
            token_trap=token_trap,
        ),
        evaluation_metrics=EvaluationMetricsConfig(
            fail_on_infinite_loop=True,
            min_resiliency_score=0.82,
        ),
    )


def _parse_iso(iso_str: str) -> datetime:
    """Parse ISO datetime tolerating trailing 'Z'."""
    normalized = iso_str.strip()
    if normalized.endswith("Z") or normalized.endswith("z"):
        normalized = normalized[:-1] + "+00:00"
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


# ============================================================================
# 1. Strategy Pipeline Builder Tests
# ============================================================================

def test_build_strategies_none_config_returns_empty():
    """None config yields an empty strategy list."""
    assert build_strategies(None) == []


def test_build_strategies_omits_disabled_profiles():
    """Disabled strategy profiles are filtered out from the pipeline."""
    cfg = _make_config(
        network_chaos=NetworkChaosConfig(enabled=False),
        schema_mutilation=SchemaMutilationConfig(enabled=True),
        token_trap=TokenTrapConfig(enabled=False),
    )
    strategies = build_strategies(cfg)
    assert len(strategies) == 1
    assert isinstance(strategies[0], SchemaMutilationStrategy)
    assert strategies[0].name == "schema_mutilation"


def test_build_strategies_fixed_pipeline_order_when_all_enabled():
    """When all profiles are enabled, the order must be strictly:
    token_trap -> rbac_jailbreak -> network_chaos -> schema_mutilation -> semantic_mirage.
    """
    cfg = _make_config(
        token_trap=TokenTrapConfig(enabled=True, max_cyclic_depth=3, trap_paths=["/tool/a"]),
        rbac_jailbreaker=RbacJailbreakerConfig(enabled=True, protected_fields=["secret"]),
        network_chaos=NetworkChaosConfig(enabled=True, http_503_injection_rate=0.1),
        schema_mutilation=SchemaMutilationConfig(enabled=True, drop_field_rate=0.1),
        semantic_mirage=SemanticMirageConfig(enabled=True, corruption_rate=1.0),
    )
    strategies = build_strategies(cfg)
    expected_order = [
        "token_trap",
        "rbac_jailbreak",
        "network_chaos",
        "schema_mutilation",
        "semantic_mirage",
    ]
    actual_order = [s.name for s in strategies]
    assert actual_order == expected_order

    expected_types = [
        TokenTrapStrategy,
        RbacJailbreakStrategy,
        NetworkChaosStrategy,
        SchemaMutilationStrategy,
        SemanticMirageStrategy,
    ]
    for instance, expected_cls in zip(strategies, expected_types):
        assert isinstance(instance, expected_cls)


# ============================================================================
# 2. NetworkChaosStrategy Tests
# ============================================================================

def test_network_chaos_should_apply_disabled_or_missing():
    strategy = NetworkChaosStrategy(rng=random.Random(42))
    req = httpx.Request("GET", "http://upstream.test/data")

    # Missing profile
    cfg_missing = _make_config()
    assert strategy.should_apply(req, cfg_missing) is False

    # Disabled profile
    cfg_disabled = _make_config(network_chaos=NetworkChaosConfig(enabled=False))
    assert strategy.should_apply(req, cfg_disabled) is False

    # Enabled profile
    cfg_enabled = _make_config(network_chaos=NetworkChaosConfig(enabled=True))
    assert strategy.should_apply(req, cfg_enabled) is True


@pytest.mark.asyncio
async def test_network_chaos_503_injection_rate_one_raises_short_circuit():
    strategy = NetworkChaosStrategy(rng=random.Random(42))
    req = httpx.Request("GET", "http://upstream.test/data")
    cfg = _make_config(
        network_chaos=NetworkChaosConfig(
            enabled=True,
            severity=0.85,
            http_503_injection_rate=1.0,
        )
    )

    with pytest.raises(ShortCircuitFault) as exc_info:
        await strategy.mutate_request(req, cfg)

    fault = exc_info.value
    assert fault.fault_name == "network_chaos"
    assert fault.severity == 0.85
    assert fault.response.status_code == 503
    body = json.loads(fault.response.content)
    assert body["error"] == "Service Unavailable"
    assert body["injected_by"] == "network_chaos"


@pytest.mark.asyncio
async def test_network_chaos_500_injection_rate_one_raises_short_circuit():
    strategy = NetworkChaosStrategy(rng=random.Random(42))
    req = httpx.Request("GET", "http://upstream.test/data")
    cfg = _make_config(
        network_chaos=NetworkChaosConfig(
            enabled=True,
            http_500_injection_rate=1.0,
        )
    )

    with pytest.raises(ShortCircuitFault) as exc_info:
        await strategy.mutate_request(req, cfg)

    assert exc_info.value.response.status_code == 500
    body = json.loads(exc_info.value.response.content)
    assert body["error"] == "Internal Server Error"


@pytest.mark.asyncio
async def test_network_chaos_429_carries_retry_after_header():
    strategy = NetworkChaosStrategy(rng=random.Random(42))
    req = httpx.Request("GET", "http://upstream.test/data")
    cfg = _make_config(
        network_chaos=NetworkChaosConfig(
            enabled=True,
            http_429_injection_rate=1.0,
        )
    )

    with pytest.raises(ShortCircuitFault) as exc_info:
        await strategy.mutate_request(req, cfg)

    resp = exc_info.value.response
    assert resp.status_code == 429
    assert "retry-after" in resp.headers
    retry_after_val = int(resp.headers["retry-after"])
    assert 1 <= retry_after_val <= 60


@pytest.mark.asyncio
async def test_network_chaos_error_precedence_503_over_500_over_429():
    strategy = NetworkChaosStrategy(rng=random.Random(42))
    req = httpx.Request("GET", "http://upstream.test/data")

    # When 503, 500, and 429 all have rate 1.0, 503 must take precedence
    cfg_all = _make_config(
        network_chaos=NetworkChaosConfig(
            enabled=True,
            http_503_injection_rate=1.0,
            http_500_injection_rate=1.0,
            http_429_injection_rate=1.0,
        )
    )
    with pytest.raises(ShortCircuitFault) as exc_503:
        await strategy.mutate_request(req, cfg_all)
    assert exc_503.value.response.status_code == 503

    # When 503 is 0.0, but 500 and 429 are 1.0, 500 must take precedence
    cfg_500_429 = _make_config(
        network_chaos=NetworkChaosConfig(
            enabled=True,
            http_503_injection_rate=0.0,
            http_500_injection_rate=1.0,
            http_429_injection_rate=1.0,
        )
    )
    with pytest.raises(ShortCircuitFault) as exc_500:
        await strategy.mutate_request(req, cfg_500_429)
    assert exc_500.value.response.status_code == 500


@pytest.mark.asyncio
async def test_network_chaos_all_zero_rates_mutates_nothing():
    strategy = NetworkChaosStrategy(rng=random.Random(42))
    req = httpx.Request("POST", "http://upstream.test/data", content=b"payload")
    cfg = _make_config(
        network_chaos=NetworkChaosConfig(
            enabled=True,
            http_503_injection_rate=0.0,
            http_500_injection_rate=0.0,
            http_429_injection_rate=0.0,
            latency_injection_rate=0.0,
        )
    )

    mutated_req = await strategy.mutate_request(req, cfg)
    assert mutated_req is req
    assert mutated_req.content == b"payload"


@pytest.mark.asyncio
async def test_network_chaos_latency_injection_actually_delays():
    strategy = NetworkChaosStrategy(rng=random.Random(42))
    req = httpx.Request("GET", "http://upstream.test/data")
    cfg = _make_config(
        network_chaos=NetworkChaosConfig(
            enabled=True,
            latency_injection_rate=1.0,
            latency_range_ms=(50, 60),
        )
    )

    t0 = time.perf_counter()
    res = await strategy.mutate_request(req, cfg)
    elapsed = time.perf_counter() - t0

    assert res is req
    assert elapsed >= 0.045, f"Expected delay >= 0.045s, got {elapsed:.4f}s"


@pytest.mark.asyncio
async def test_network_chaos_mutate_response_always_returns_none():
    strategy = NetworkChaosStrategy(rng=random.Random(42))
    cfg = _make_config(network_chaos=NetworkChaosConfig(enabled=True))
    resp = httpx.Response(200, content=b'{"ok": true}')

    result = await strategy.mutate_response(resp, cfg)
    assert result is None


# ============================================================================
# 3. SchemaMutilationStrategy Tests
# ============================================================================

def test_schema_mutilation_should_apply():
    strategy = SchemaMutilationStrategy(rng=random.Random(42))
    req = httpx.Request("GET", "http://upstream.test/data")

    assert strategy.should_apply(req, _make_config()) is False
    assert strategy.should_apply(req, _make_config(schema_mutilation=SchemaMutilationConfig(enabled=False))) is False
    assert strategy.should_apply(req, _make_config(schema_mutilation=SchemaMutilationConfig(enabled=True))) is True


@pytest.mark.asyncio
async def test_schema_mutilation_drop_field_rate_one():
    strategy = SchemaMutilationStrategy(rng=random.Random(42))
    cfg = _make_config(
        schema_mutilation=SchemaMutilationConfig(
            enabled=True,
            drop_field_rate=1.0,
            target_fields=["target_key"],
        )
    )
    initial_body = json.dumps({"target_key": 42, "keep_me": "value"}).encode("utf-8")
    resp = httpx.Response(200, headers={"content-type": "application/json"}, content=initial_body)

    mutated = await strategy.mutate_response(resp, cfg)
    assert mutated is not None
    data = json.loads(mutated.content)
    assert "target_key" not in data
    assert data["keep_me"] == "value"


@pytest.mark.asyncio
async def test_schema_mutilation_null_injection_rate_one():
    strategy = SchemaMutilationStrategy(rng=random.Random(42))
    cfg = _make_config(
        schema_mutilation=SchemaMutilationConfig(
            enabled=True,
            null_injection_rate=1.0,
            target_fields=["nulled_key"],
        )
    )
    initial_body = json.dumps({"nulled_key": "will_be_none", "keep": 100}).encode("utf-8")
    resp = httpx.Response(200, headers={"content-type": "application/json"}, content=initial_body)

    mutated = await strategy.mutate_response(resp, cfg)
    assert mutated is not None
    data = json.loads(mutated.content)
    assert data["nulled_key"] is None
    assert data["keep"] == 100


@pytest.mark.asyncio
async def test_schema_mutilation_type_confusion_rate_one():
    strategy = SchemaMutilationStrategy(rng=random.Random(42))
    cfg = _make_config(
        schema_mutilation=SchemaMutilationConfig(
            enabled=True,
            type_confusion_rate=1.0,
            target_fields=["int_to_str", "str_to_int"],
        )
    )
    initial_body = json.dumps({"int_to_str": 999, "str_to_int": "1234"}).encode("utf-8")
    resp = httpx.Response(200, headers={"content-type": "application/json"}, content=initial_body)

    mutated = await strategy.mutate_response(resp, cfg)
    assert mutated is not None
    data = json.loads(mutated.content)
    assert data["int_to_str"] == "999"
    assert data["str_to_int"] == 1234


@pytest.mark.asyncio
async def test_schema_mutilation_all_zero_rates_returns_none():
    strategy = SchemaMutilationStrategy(rng=random.Random(42))
    cfg = _make_config(
        schema_mutilation=SchemaMutilationConfig(
            enabled=True,
            drop_field_rate=0.0,
            type_confusion_rate=0.0,
            null_injection_rate=0.0,
        )
    )
    resp = httpx.Response(200, headers={"content-type": "application/json"}, content=b'{"a": 1}')

    mutated = await strategy.mutate_response(resp, cfg)
    assert mutated is None


@pytest.mark.asyncio
async def test_schema_mutilation_nested_dict_at_depth():
    strategy = SchemaMutilationStrategy(rng=random.Random(42))
    cfg = _make_config(
        schema_mutilation=SchemaMutilationConfig(
            enabled=True,
            null_injection_rate=1.0,
            target_fields=["deep_secret"],
        )
    )
    initial_body = json.dumps({
        "level1": {
            "level2": {
                "deep_secret": "classified",
                "sibling": "untouched",
            }
        }
    }).encode("utf-8")
    resp = httpx.Response(200, headers={"content-type": "application/json"}, content=initial_body)

    mutated = await strategy.mutate_response(resp, cfg)
    assert mutated is not None
    data = json.loads(mutated.content)
    assert data["level1"]["level2"]["deep_secret"] is None
    assert data["level1"]["level2"]["sibling"] == "untouched"


@pytest.mark.asyncio
async def test_schema_mutilation_non_json_body_returns_none_without_raising():
    strategy = SchemaMutilationStrategy(rng=random.Random(42))
    cfg = _make_config(
        schema_mutilation=SchemaMutilationConfig(
            enabled=True,
            drop_field_rate=1.0,
        )
    )
    resp = httpx.Response(200, headers={"content-type": "text/plain"}, content=b"not a valid json payload")

    mutated = await strategy.mutate_response(resp, cfg)
    assert mutated is None


@pytest.mark.asyncio
async def test_schema_mutilation_strips_content_length_and_encoding():
    import gzip

    strategy = SchemaMutilationStrategy(rng=random.Random(42))
    cfg = _make_config(
        schema_mutilation=SchemaMutilationConfig(
            enabled=True,
            drop_field_rate=1.0,
            target_fields=["drop_me"],
        )
    )
    raw_body = b'{"drop_me": 1, "stay": 2}'
    compressed = gzip.compress(raw_body)
    resp = httpx.Response(
        200,
        headers={
            "content-type": "application/json",
            "content-length": str(len(compressed)),
            "content-encoding": "gzip",
            "x-custom-header": "preserve-this",
        },
        content=compressed,
    )

    mutated = await strategy.mutate_response(resp, cfg)
    assert mutated is not None
    assert "content-length" not in mutated.headers
    assert "content-encoding" not in mutated.headers
    assert mutated.headers["x-custom-header"] == "preserve-this"


# ============================================================================
# 4. SemanticMirageStrategy Tests
# ============================================================================

def test_semantic_mirage_should_apply():
    strategy = SemanticMirageStrategy(rng=random.Random(42))
    req = httpx.Request("GET", "http://upstream.test/query")

    assert strategy.should_apply(req, _make_config()) is False
    assert strategy.should_apply(req, _make_config(semantic_mirage=SemanticMirageConfig(enabled=False))) is False
    assert strategy.should_apply(req, _make_config(semantic_mirage=SemanticMirageConfig(enabled=True))) is True


@pytest.mark.asyncio
async def test_semantic_mirage_temporal_anomaly_invariant():
    """Proposal core invariant: with temporal anomaly enabled and corruption_rate=1.0,
    last_updated must strictly precede created_at on mutated records.
    """
    strategy = SemanticMirageStrategy(rng=random.Random(42))
    cfg = _make_config(
        semantic_mirage=SemanticMirageConfig(
            enabled=True,
            temporal_anomaly_injection=True,
            corruption_rate=1.0,
            created_at_field="created_at",
            updated_at_field="last_updated",
        )
    )
    initial_body = json.dumps({
        "id": 101,
        "created_at": "2026-09-09T12:00:00Z",
        "last_updated": "2026-09-09T14:30:00Z",
    }).encode("utf-8")
    resp = httpx.Response(200, headers={"content-type": "application/json"}, content=initial_body)

    mutated = await strategy.mutate_response(resp, cfg)
    assert mutated is not None
    assert mutated.status_code == 200

    data = json.loads(mutated.content)
    dt_created = _parse_iso(data["created_at"])
    dt_updated = _parse_iso(data["last_updated"])

    # Strict temporal inversion invariant
    assert dt_updated < dt_created, (
        f"Temporal invariant violated: last_updated ({dt_updated}) >= created_at ({dt_created})"
    )


@pytest.mark.asyncio
async def test_semantic_mirage_role_mutation_overwrites_field():
    strategy = SemanticMirageStrategy(rng=random.Random(42))
    cfg = _make_config(
        semantic_mirage=SemanticMirageConfig(
            enabled=True,
            corruption_rate=1.0,
            role_field="user_role",
            role_mutation_value="admin",
        )
    )
    initial_body = json.dumps({"username": "alice", "user_role": "viewer"}).encode("utf-8")
    resp = httpx.Response(200, headers={"content-type": "application/json"}, content=initial_body)

    mutated = await strategy.mutate_response(resp, cfg)
    assert mutated is not None
    assert mutated.status_code == 200
    data = json.loads(mutated.content)
    assert data["user_role"] == "admin"
    assert data["username"] == "alice"


@pytest.mark.asyncio
async def test_semantic_mirage_corruption_rate_zero_returns_none():
    strategy = SemanticMirageStrategy(rng=random.Random(42))
    cfg = _make_config(
        semantic_mirage=SemanticMirageConfig(
            enabled=True,
            temporal_anomaly_injection=True,
            corruption_rate=0.0,
            role_field="user_role",
            role_mutation_value="admin",
        )
    )
    resp = httpx.Response(200, headers={"content-type": "application/json"}, content=b'{"user_role": "viewer"}')
    mutated = await strategy.mutate_response(resp, cfg)
    assert mutated is None


@pytest.mark.asyncio
async def test_semantic_mirage_row_lacking_timestamp_fields_untouched_no_raise():
    strategy = SemanticMirageStrategy(rng=random.Random(42))
    cfg = _make_config(
        semantic_mirage=SemanticMirageConfig(
            enabled=True,
            temporal_anomaly_injection=True,
            corruption_rate=1.0,
        )
    )
    initial_body = json.dumps({"name": "untouched", "status": "active"}).encode("utf-8")
    resp = httpx.Response(200, headers={"content-type": "application/json"}, content=initial_body)

    mutated = await strategy.mutate_response(resp, cfg)
    assert mutated is None


# ============================================================================
# 5. RbacJailbreakStrategy Tests
# ============================================================================

def test_rbac_jailbreak_should_apply():
    strategy = RbacJailbreakStrategy(rng=random.Random(42))
    req = httpx.Request("POST", "http://upstream.test/sql")

    assert strategy.should_apply(req, _make_config()) is False
    assert strategy.should_apply(req, _make_config(rbac_jailbreaker=RbacJailbreakerConfig(enabled=False))) is False
    assert strategy.should_apply(req, _make_config(rbac_jailbreaker=RbacJailbreakerConfig(enabled=True))) is True


@pytest.mark.asyncio
async def test_rbac_jailbreak_whitespace_and_case_normalized_matching():
    """Normalized query matching must trigger a 403 ShortCircuitFault echoing the rule."""
    strategy = RbacJailbreakStrategy(rng=random.Random(42))
    rule = "UPDATE users SET user_role = 'admin'"
    cfg = _make_config(
        rbac_jailbreaker=RbacJailbreakerConfig(
            enabled=True,
            severity=0.9,
            prohibit_mutations=[rule],
        )
    )

    # Malicious payload with arbitrary spacing and different capitalization
    payload = b"UPDATE  users   SET user_role='admin'"
    req = httpx.Request("POST", "http://upstream.test/execute", content=payload)

    with pytest.raises(ShortCircuitFault) as exc_info:
        await strategy.mutate_request(req, cfg)

    fault = exc_info.value
    assert fault.fault_name == "rbac_jailbreak"
    assert fault.severity == 0.9
    assert fault.response.status_code == 403

    body = json.loads(fault.response.content)
    assert body["error"] == "rbac_violation"
    assert body["blocked_rule"] == rule


@pytest.mark.asyncio
async def test_rbac_jailbreak_benign_get_passes_untouched():
    strategy = RbacJailbreakStrategy(rng=random.Random(42))
    cfg = _make_config(
        rbac_jailbreaker=RbacJailbreakerConfig(
            enabled=True,
            prohibit_mutations=["DROP TABLE users"],
            protected_fields=["password_hash"],
        )
    )
    req = httpx.Request("GET", "http://upstream.test/users/42")

    res = await strategy.mutate_request(req, cfg)
    assert res is req


@pytest.mark.asyncio
async def test_rbac_jailbreak_mutate_response_redacts_protected_fields_at_depth():
    strategy = RbacJailbreakStrategy(rng=random.Random(42))
    cfg = _make_config(
        rbac_jailbreaker=RbacJailbreakerConfig(
            enabled=True,
            protected_fields=["password_hash", "ssn"],
        )
    )
    initial_body = json.dumps({
        "username": "bob",
        "password_hash": "argon2$hashvalue",
        "meta": {
            "ssn": "000-12-3456",
            "department": "Engineering",
        },
    }).encode("utf-8")
    resp = httpx.Response(200, headers={"content-type": "application/json"}, content=initial_body)

    mutated = await strategy.mutate_response(resp, cfg)
    assert mutated is not None
    data = json.loads(mutated.content)
    assert data["password_hash"] == "[REDACTED]"
    assert data["meta"]["ssn"] == "[REDACTED]"
    assert data["username"] == "bob"
    assert data["meta"]["department"] == "Engineering"


@pytest.mark.asyncio
async def test_rbac_jailbreak_mutate_response_nothing_to_redact_returns_none():
    strategy = RbacJailbreakStrategy(rng=random.Random(42))
    cfg = _make_config(
        rbac_jailbreaker=RbacJailbreakerConfig(
            enabled=True,
            protected_fields=["credit_card"],
        )
    )
    initial_body = json.dumps({"user": "charlie", "tier": "free"}).encode("utf-8")
    resp = httpx.Response(200, headers={"content-type": "application/json"}, content=initial_body)

    mutated = await strategy.mutate_response(resp, cfg)
    assert mutated is None


# ============================================================================
# 6. TokenTrapStrategy Tests
# ============================================================================

def test_token_trap_should_apply():
    strategy = TokenTrapStrategy(rng=random.Random(42))
    cfg = _make_config(
        token_trap=TokenTrapConfig(
            enabled=True,
            max_cyclic_depth=3,
            trap_paths=["/tools/loop_a", "/tools/loop_b"],
        )
    )

    req_trap = httpx.Request("POST", "http://upstream.test/tools/loop_a")
    assert strategy.should_apply(req_trap, cfg) is True

    req_non_trap = httpx.Request("POST", "http://upstream.test/safe/path")
    assert strategy.should_apply(req_non_trap, cfg) is False

    cfg_empty_traps = _make_config(
        token_trap=TokenTrapConfig(enabled=True, max_cyclic_depth=3, trap_paths=[])
    )
    assert strategy.should_apply(req_trap, cfg_empty_traps) is False


@pytest.mark.asyncio
async def test_token_trap_cyclic_depth_and_round_robin():
    strategy = TokenTrapStrategy(rng=random.Random(42))
    traps = ["/tools/first", "/tools/second"]
    cfg = _make_config(
        token_trap=TokenTrapConfig(
            enabled=True,
            severity=0.6,
            max_cyclic_depth=3,
            trap_paths=traps,
            redirect_message="Redirecting to {next_path}",
        )
    )

    req = httpx.Request("POST", "http://upstream.test/tools/first")

    # Calls 1, 2, 3 must raise ShortCircuitFault with status 200, incrementing depth
    for expected_depth in (1, 2, 3):
        with pytest.raises(ShortCircuitFault) as exc_info:
            await strategy.mutate_request(req, cfg)
        fault = exc_info.value
        assert fault.fault_name == "token_trap"
        assert fault.severity == 0.6
        assert fault.response.status_code == 200
        body = json.loads(fault.response.content)
        assert body["cycle_depth"] == expected_depth
        assert body["next_tool"] == "/tools/second"
        assert body["message"] == "Redirecting to /tools/second"

    # Call 4: trap exhausted (count 4 > max 3), request passes through UNCHANGED
    escaped = await strategy.mutate_request(req, cfg)
    assert escaped is req

    # Resetting the trap restores trapping starting at depth 1
    strategy.reset()
    with pytest.raises(ShortCircuitFault) as exc_after_reset:
        await strategy.mutate_request(req, cfg)
    assert json.loads(exc_after_reset.value.response.content)["cycle_depth"] == 1


# ============================================================================
# 7. Regression Tests for Defect Fixes
# ============================================================================

@pytest.mark.parametrize(
    "body_text",
    [
        "UPDATE users SET user_role = 'admin'",
        "update   USERS   set user_role='admin'",
        "UPDATE/**/users/**/SET/**/user_role/**/='admin'",
        "UPDATE users --evil\nSET user_role = 'admin'",
        "UPDATE users #evil\nSET user_role = 'admin'",
        "UPDATE users SET user_role = ‘admin’",
    ],
)
@pytest.mark.asyncio
async def test_rbac_obfuscation_bypass_triggers_403(body_text: str):
    """Regression test (Defect 1): SQL comment stripping, quote folding, and whitespace
    normalization prevent obfuscation bypass against prohibit_mutations rules.
    """
    rule = "UPDATE users SET user_role = 'admin'"
    cfg = _make_config(
        rbac_jailbreaker=RbacJailbreakerConfig(
            enabled=True,
            prohibit_mutations=[rule],
            protected_fields=[],
        )
    )
    strategy = RbacJailbreakStrategy(rng=random.Random(42))
    req = httpx.Request("POST", "http://upstream.test/query", content=body_text.encode("utf-8"))

    with pytest.raises(ShortCircuitFault) as exc_info:
        await strategy.mutate_request(req, cfg)

    fault = exc_info.value
    assert fault.fault_name == "rbac_jailbreak"
    assert fault.response.status_code == 403
    payload = json.loads(fault.response.content)
    assert payload["error"] == "rbac_violation"
    assert payload["blocked_rule"] == rule
    assert payload["injected_by"] == "rbac_jailbreak"


@pytest.mark.asyncio
async def test_rbac_benign_post_body_not_blocked():
    """Negative case (Defect 1): benign POST body must not trigger false-positive blocking."""
    rule = "UPDATE users SET user_role = 'admin'"
    cfg = _make_config(
        rbac_jailbreaker=RbacJailbreakerConfig(
            enabled=True,
            prohibit_mutations=[rule],
            protected_fields=[],
        )
    )
    strategy = RbacJailbreakStrategy(rng=random.Random(42))
    benign_req = httpx.Request(
        "POST",
        "http://upstream.test/query",
        content=b"UPDATE orders SET status = 'shipped'",
    )

    res = await strategy.mutate_request(benign_req, cfg)
    assert res is benign_req


@pytest.mark.asyncio
async def test_rbac_redaction_depth_leak_prevents_secret_exposure():
    """Regression test (Defect 2): redaction must recurse past depth 5 (up to depth 100).
    A password_hash at depth 7 and depth 12 must both be replaced with '[REDACTED]',
    sibling non-protected keys at the same depth must survive, and mutate_response
    must NOT return None (which was the leak mechanism).
    """
    strategy = RbacJailbreakStrategy(rng=random.Random(42))
    cfg = _make_config(
        rbac_jailbreaker=RbacJailbreakerConfig(
            enabled=True,
            protected_fields=["password_hash"],
        )
    )

    # Construct nested structure with password_hash and sibling at depth 7 and depth 12
    payload: dict = {"level_1": {}}
    curr = payload["level_1"]
    for d in range(2, 7):
        curr[f"level_{d}"] = {}
        curr = curr[f"level_{d}"]

    curr["password_hash"] = "secret_hash_depth_7"
    curr["sibling_7"] = "public_val_7"
    curr["level_7"] = {}
    curr = curr["level_7"]

    for d in range(8, 12):
        curr[f"level_{d}"] = {}
        curr = curr[f"level_{d}"]

    curr["password_hash"] = "secret_hash_depth_12"
    curr["sibling_12"] = "public_val_12"

    raw_response = httpx.Response(
        status_code=200,
        headers={"content-type": "application/json"},
        content=json.dumps(payload).encode("utf-8"),
    )

    mutated = await strategy.mutate_response(raw_response, cfg)

    # Returning None was the exact leak mechanism: the pipeline forwarded unredacted body
    assert mutated is not None, "mutate_response returned None, leaking unredacted secrets at depth > 5"

    data = json.loads(mutated.content)

    # Verify depth 7
    node_7 = data["level_1"]["level_2"]["level_3"]["level_4"]["level_5"]["level_6"]
    assert node_7["password_hash"] == "[REDACTED]"
    assert node_7["sibling_7"] == "public_val_7"

    # Verify depth 12
    node_12 = node_7["level_7"]["level_8"]["level_9"]["level_10"]["level_11"]
    assert node_12["password_hash"] == "[REDACTED]"
    assert node_12["sibling_12"] == "public_val_12"


@pytest.mark.asyncio
async def test_token_trap_memory_bound_eviction_and_fresh_path_cycling():
    """Regression test (Defect 3): _hit_counts must be capped at 10_000 entries via FIFO eviction,
    and a fresh path must cycle correctly through cycle_depth 1, 2, 3 and pass-through at 4.
    """
    strategy = TokenTrapStrategy(rng=random.Random(42))
    traps = ["/loop"]
    cfg = _make_config(
        token_trap=TokenTrapConfig(
            enabled=True,
            max_cyclic_depth=3,
            trap_paths=traps,
            redirect_message="Redirecting to {next_path}",
        )
    )

    # Feed 10_100 distinct trap paths under /loop
    for i in range(10_100):
        req = httpx.Request("GET", f"http://upstream.test/loop/path_{i}")
        try:
            await strategy.mutate_request(req, cfg)
        except ShortCircuitFault:
            pass

    assert len(strategy._hit_counts) <= 10_000, (
        f"Memory leak in _hit_counts: expected <= 10000 entries, got {len(strategy._hit_counts)}"
    )

    # Fresh path must cycle through depths 1, 2, 3 then pass-through at call 4
    fresh_req = httpx.Request("GET", "http://upstream.test/loop/fresh_path")
    for expected_depth in (1, 2, 3):
        with pytest.raises(ShortCircuitFault) as exc_info:
            await strategy.mutate_request(fresh_req, cfg)
        fault = exc_info.value
        assert fault.fault_name == "token_trap"
        assert fault.response.status_code == 200
        body = json.loads(fault.response.content)
        assert body["cycle_depth"] == expected_depth

    # Call 4: trap exhausted (count 4 > max 3), passes through unchanged
    escaped = await strategy.mutate_request(fresh_req, cfg)
    assert escaped is fresh_req
