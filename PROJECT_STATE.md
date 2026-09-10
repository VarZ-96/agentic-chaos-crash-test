# PROJECT STATE & AGENT CONTEXT
**Last Updated:** [09-09-2026]

## 1. Current Phase
* Phases 1 and 2 are complete. We have officially transitioned to **Phase 3: DAG Graph Tracker & Metrics**.
* The asynchronous reverse proxy, the HTTP request/response logging engine, and all five chaos mutation engines are implemented and verified end-to-end against a live upstream.

## 2. Completed Milestones
* [x] Generate the foundational directory structure (api, core, models, schemas, services, repositories, utils).
* [x] Create `requirements.txt` with base async dependencies (FastAPI, httpx, pydantic, asyncpg).
* [x] Write the `main.py` entrypoint.
* [x] Write `app/core/config.py` to parse environment variables and the `chaos.yaml` Pydantic schema.
* [x] Extend the `chaos.yaml` Pydantic schema with `upstream_base_url`, per-profile `severity`, bounded injection rates, and a `schema_mutilation` profile; `extra="forbid"` so config typos fail loudly.
* [x] Implement upstream resolution precedence (`X-Chaos-Upstream` header > `UPSTREAM_BASE_URL` env > `chaos.yaml`) in `Settings.resolve_upstream`.
* [x] Implement domain exceptions (`app/core/exceptions.py`), including `ShortCircuitFault` as the sole mechanism for answering without contacting the upstream.
* [x] Implement the in-memory execution state (`app/models/state.py`: `InterceptContext`, `FaultRecord`).
* [x] Implement stateless utilities: SHA-256 state signatures / body hashes (`app/utils/hashing.py`) and single-line structured JSON logging (`app/utils/logger.py`).
* [x] Implement the trace Pydantic DTOs (`app/schemas/trace.py`).
* [x] **Phase 1** — Implement the core asynchronous request forwarding pipeline (`app/services/proxy_service.py`) with hop-by-hop header hygiene, Host rewriting to the upstream authority, per-session monotonic step indexing, and strategy fault isolation.
* [x] **Phase 1** — Implement the HTTP request/response logging engine: append-only trace persistence with batched, fire-and-forget, non-blocking writes (`app/repositories/trace_repo.py`) plus run metrics (`app/repositories/metrics_repo.py`), on aiosqlite by default and asyncpg when `DATABASE_DSN` is set. Raw parameterized SQL only.
* [x] **Phase 1** — Implement the API layer with zero business logic: `/v1/{path:path}` catch-all interceptor, `/v1/traces` query & purge, `/v1/traces/runs/{session_id}`, `/v1/chaos/config` (GET/PUT) and `/v1/chaos/reload`, plus the `app/api/deps.py` DI container.
* [x] **Phase 1** — Wire the ASGI lifespan object graph in `main.py` (shared pooled `httpx.AsyncClient`, repositories, strategy chain) with exception-safe teardown, and extend `GET /health` to report loaded config and active strategies.
* [x] **Phase 2** — Implement the `BaseChaosStrategy` Strategy-pattern contract and the `build_strategies` factory enforcing the fixed chain order `token_trap, rbac_jailbreak, network_chaos, schema_mutilation, semantic_mirage`.
* [x] **Phase 2** — Module 1: latency injection and HTTP 500/503/429 fault injection (`network_chaos.py`), with `Retry-After` on 429 and `asyncio.sleep`-based latency curves.
* [x] **Phase 2** — Schema mutilation: field dropping, type confusion, and null injection with nested traversal (`schema_mutilation.py`).
* [x] **Phase 2** — Module 2: cyclic dependency / token-burner traps with round-robin redirects and a bounded escape depth (`token_trap.py`).
* [x] **Phase 2** — Module 3: PostgreSQL payload corruption via temporal anomalies (`last_updated` forced to precede `created_at`) and role mutation, returned under a clean HTTP 200 (`semantic_mirage.py`).
* [x] **Phase 2** — Module 4: RBAC jailbreak detection blocking prohibited mutations (normalized against whitespace, case, SQL comment and smart-quote obfuscation) and redacting protected fields from responses (`rbac_jailbreak.py`).
* [x] Harden the trace store against credential exposure: sensitive request/response headers are persisted as `[REDACTED]` while still being forwarded upstream intact.
* [x] Bound all long-lived in-memory maps (`_step_indices`, `_hit_counts`, pending metrics) at 10,000 entries with insertion-order eviction.
* [x] Move run-metric persistence off the request hot path (in-memory accumulation, flushed on `flush()`/`close()`/`get_run()`).
* [x] Author the pytest suites for the proxy and the chaos strategies (`tests/test_proxy.py`, `tests/test_chaos_strategies.py`) — 59 tests passing, including regression coverage for the config hot-swap, credential redaction, and obfuscation-bypass defects.
* [x] Author `README.md` (quickstart, endpoint table, full `chaos.yaml` reference) and a multi-stage non-root `Dockerfile`.

## 3. Active Work in Progress (WIP)
* Implement the Execution Graph Engine (`app/services/dag_service.py`) and its domain entities (`app/models/dag.py`): build the time-indexed DAG *G* = (*V*, *E*) from the trace stream, and detect behavioural loops in O(1) by counting recurring state signatures (`app/utils/hashing.generate_state_signature` already provides the signature primitive).
* Implement the Quantitative Resiliency Scoring Engine (`app/utils/math_metrics.py`, `app/services/scoring_service.py`, `app/schemas/evaluation.py`): compute *RS* from task completion, mean normalised fault severity, token-waste ratio, and loop penalty, then expose it via a `/v1/traces/runs/{session_id}` evaluation response and enforce `evaluation_metrics.min_resiliency_score`.

## 4. Strict Reminders for AI
* **DO NOT** use synchronous libraries for network I/O (e.g., `requests`). Always use `httpx.AsyncClient`.
* **DO NOT** introduce heavy ORMs like SQLAlchemy. Stick to raw parameterized queries with `asyncpg`.
* **DO NOT** write monolithic functions. Enforce a strict separation of concerns between Controllers (routing), Services (logic), and Repositories (data).
* Review `ARCHITECTURE.md` before making any major structural changes.
