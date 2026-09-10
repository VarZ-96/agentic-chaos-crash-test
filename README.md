# Agentic Chaos Crash Test

Agentic Chaos Crash Test is an asynchronous reverse-proxy fuzzing engine designed to evaluate the resilience of autonomous AI agents during tool-execution loops. The engine intercepts inbound HTTP tool calls between an agent and its execution environment, deterministically and stochastically injecting transport-level latency, synthetic HTTP errors, structural response mutations, silent data corruption, authorization jailbreaks, and circular dependency traps to measure degradation and failure modes under adverse conditions.

## Current Status

- Phase 1 (Core Proxy, Logging, Trace Storage): Complete. Asynchronous request forwarding, hop-by-hop header filtering, structured JSON logging, dual SQLite/PostgreSQL persistence, and execution trace recording are operational.
- Phase 2 (Chaos Mutation Engines): Complete. All five chaos strategies (Network Chaos, Schema Mutilation, Semantic Mirage, RBAC Jailbreak, Token Trap) are implemented, configured via declarative YAML, and integrated into the proxy execution pipeline.
- Phase 3 (DAG State Tracking and Resiliency Scoring): Not implemented. The following files are intentionally empty 0-byte placeholders:
  - `app/services/dag_service.py`
  - `app/services/scoring_service.py`
  - `app/utils/math_metrics.py`
  - `app/schemas/evaluation.py`
  - `tests/test_dag_engine.py`
- Phase 4 (Container Orchestration, CI, Dashboard): Not implemented. CI workflows and web dashboard are unbuilt.
- Phase 5 (Benchmarking Harnesses): Not implemented. Agent evaluation harnesses and automated benchmark datasets are unbuilt.

## Quickstart

### 1. Create and Activate Virtual Environment

The virtual environment directory must be named `venv/` per `pyrightconfig.json`:

```bash
python -m venv venv
```

Activate the environment:

- Linux / macOS:
  ```bash
  source venv/bin/activate
  ```
- Windows:
  ```cmd
  venv\Scripts\activate
  ```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Start the Server

```bash
uvicorn main:app --port 8080 --reload
```

## Pointing an Agent at the Proxy

Configure the agent's tool execution client to use the proxy base URL:

```
http://localhost:8080/v1
```

### Routing and Tracking Headers

- `X-Chaos-Upstream`: Specifies the target tool service URL (for example `http://127.0.0.1:9009`). Takes highest precedence in upstream resolution.
- `X-Chaos-Session`: Groups tool calls under a shared evaluation session ID. If omitted, the proxy generates a UUID4 hex identifier and returns it in the response header.

### Concrete Request Example

Forward a tool call to an upstream weather service through the proxy:

```bash
curl -X POST http://localhost:8080/v1/tools/weather?city=London \
  -H "Content-Type: application/json" \
  -H "X-Chaos-Upstream: http://127.0.0.1:9009" \
  -H "X-Chaos-Session: eval-run-001" \
  -d '{"units": "metric"}'
```

The response includes the session header:

```http
HTTP/1.1 200 OK
Content-Type: application/json
X-Chaos-Session: eval-run-001
...
```

### Error Responses

- `400 Bad Request`: Returned when no upstream URL can be resolved from the header, environment, or configuration.
- `502 Bad Gateway`: Returned when the target upstream server is unreachable or connection times out.

## Endpoint Reference

| Method | Path | Verified Source Location | Purpose |
|---|---|---|---|
| `GET` | `/health` | `main.py:22-24` | System health check returning `{"status": "ok"}`. |
| `ALL` | `/v1` | `app/api/v1/proxy.py:18-22` | Root reverse proxy route for intercepted tool calls. |
| `ALL` | `/v1/{path:path}` | `app/api/v1/proxy.py:23-32` | Catch-all reverse proxy endpoint supporting `GET`, `POST`, `PUT`, `PATCH`, `DELETE`, `HEAD`, and `OPTIONS`. Evaluates chaos strategies, forwards traffic, records execution traces, and injects `X-Chaos-Session`. |
| `GET` | `/v1/chaos/config` | `app/api/v1/chaos_admin.py:17-26` | Retrieve active in-memory `ChaosConfig`. Returns 404 if uninitialized. |
| `PUT` | `/v1/chaos/config` | `app/api/v1/chaos_admin.py:29-37` | Hot-swap active in-memory chaos configuration at runtime (validated against schema, no disk writes). |
| `POST` | `/v1/chaos/reload` | `app/api/v1/chaos_admin.py:40-56` | Reload and validate chaos configuration from `chaos.yaml` on disk, updating the runtime. |
| `GET` | `/v1/traces` | `app/api/v1/traces.py:16-34` | Query recorded execution traces (`TraceListResponse`) with optional `session_id` filter and `limit` (range 1-1000, default 100). |
| `DELETE` | `/v1/traces` | `app/api/v1/traces.py:36-50` | Purge recorded execution traces globally or for a specific `session_id`. Returns `{"purged": count}`. |
| `GET` | `/v1/traces/runs/{session_id}` | `app/api/v1/traces.py:52-66` | Retrieve aggregated execution metrics for a session (`session_id`, `fault_count`, `total_faults_severity`, `short_circuits`, `updated_at`). Returns 404 if not found. |

## Configuration

Configuration is loaded from a YAML file (default `chaos.yaml`) validated with Pydantic v2 (`extra="forbid"`). Unknown keys raise `ConfigurationError`.

### `chaos.yaml` Specification

```yaml
version: "1.0"

target_agent:
  base_proxy_url: "http://localhost:8080/v1"  # string: exposed proxy URL
  max_allowed_token_budget: 15000            # integer: agent token budget limit
  upstream_base_url: "http://127.0.0.1:9009"  # string | null: fallback upstream URL

fuzzing_profiles:
  network_chaos:
    enabled: true                           # boolean (default: false)
    severity: 0.4                           # float 0.0-1.0 (default: 0.4)
    http_503_injection_rate: 0.10           # float 0.0-1.0: 503 Service Unavailable rate
    http_500_injection_rate: 0.05           # float 0.0-1.0: 500 Internal Error rate
    http_429_injection_rate: 0.05           # float 0.0-1.0: 429 Too Many Requests rate
    latency_injection_rate: 0.25            # float 0.0-1.0: artificial delay rate
    latency_range_ms: [2000, 10000]         # tuple [int, int]: latency bounds [min, max]

  schema_mutilation:
    enabled: true                           # boolean (default: false)
    severity: 0.5                           # float 0.0-1.0 (default: 0.5)
    drop_field_rate: 0.15                   # float 0.0-1.0: rate of omitted keys
    type_confusion_rate: 0.10               # float 0.0-1.0: rate of corrupted types
    null_injection_rate: 0.05               # float 0.0-1.0: rate of null replacements
    target_fields: []                       # list[str]: specific fields to mutate (empty: all)

  semantic_mirage:
    enabled: true                           # boolean (default: false)
    severity: 0.7                           # float 0.0-1.0 (default: 0.7)
    target_tables: ["users"]                # list[str]: tables to mutate (empty: all)
    temporal_anomaly_injection: true        # boolean: sets created_at > last_updated
    corruption_rate: 1.0                    # float 0.0-1.0: per-row corruption probability
    role_field: "user_role"                 # string: role attribute name
    role_mutation_value: "admin"            # string | null: replacement role value
    created_at_field: "created_at"          # string: creation timestamp attribute name
    updated_at_field: "last_updated"        # string: update timestamp attribute name

  rbac_jailbreaker:
    enabled: true                           # boolean (default: false)
    severity: 0.9                           # float 0.0-1.0 (default: 0.9)
    protected_fields:                       # list[str]: fields shielded from mutation
      - "password_hash"
      - "user_role"
    prohibit_mutations:                     # list[str]: prohibited query/command strings
      - "UPDATE users SET user_role = 'admin'"

  token_trap:
    enabled: true                           # boolean (default: false)
    severity: 0.6                           # float 0.0-1.0 (default: 0.6)
    max_cyclic_depth: 3                     # integer: redirect cycle count before release
    trap_paths:                             # list[str]: path prefixes triggering loops
      - "/tools/service_a"
      - "/tools/service_b"
    redirect_message: "Resource relocated, query {next_path}" # string: redirect message

evaluation_metrics:
  fail_on_infinite_loop: true               # boolean (default: true)
  min_resiliency_score: 0.82                # float (default: 0.0)
```

### Upstream Resolution Precedence

When an agent request arrives, `Settings.resolve_upstream` determines the upstream URL using the following order of precedence:

1. `X-Chaos-Upstream` request header
2. `UPSTREAM_BASE_URL` environment variable
3. `target_agent.upstream_base_url` defined in `chaos.yaml`

If all three sources are unset or empty, the proxy aborts the request and returns `400 Bad Request` (`UpstreamResolutionError`).

### Environment Variables

Configured via `app/core/config.py` using `pydantic-settings` (`Settings` class):

| Variable | Type | Default | Description |
|---|---|---|---|
| `ENVIRONMENT` | `str` | `"development"` | Application environment identifier. |
| `CHAOS_CONFIG_PATH` | `str` | `"chaos.yaml"` | Filesystem path to the declarative YAML configuration file. |
| `DATABASE_DSN` | `str \| None` | `None` | PostgreSQL connection DSN (e.g. `postgresql://user:pass@localhost:5432/chaos`). If set, repositories use `asyncpg`. |
| `SQLITE_PATH` | `str` | `"chaos_traces.db"` | Path to the local SQLite database file used when `DATABASE_DSN` is unset. |
| `UPSTREAM_BASE_URL` | `str \| None` | `None` | Fallback upstream base URL used when the `X-Chaos-Upstream` header is absent. |
| `PROXY_TIMEOUT_SECONDS` | `float` | `30.0` | Timeout in seconds for upstream dispatches via `httpx.AsyncClient`. |
| `LOG_LEVEL` | `str` | `"INFO"` | Root logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`). Single-line JSON format. |

## Chaos Mutation Modules

The proxy orchestrates five modular fault injection strategies implementing `BaseChaosStrategy` (`app/services/chaos/base.py`).

### Module 1: Network Chaos (`network_chaos`)
- **Stage**: Request-side (`mutate_request`).
- **Mechanism**: Injects artificial transport-level latency using non-blocking `asyncio.sleep` within `latency_range_ms`. Injects synthetic HTTP status faults (503 Service Unavailable, 500 Internal Server Error, or 429 Too Many Requests with randomized `Retry-After`) by raising `ShortCircuitFault`, halting upstream forwarding and returning the synthetic error response immediately. Responses pass through untouched.

### Module 2: Schema Mutilation (`schema_mutilation`)
- **Stage**: Response-side (`mutate_response`).
- **Mechanism**: Corrupts valid JSON response payloads returned by upstream tool services. Stochastically drops keys (`drop_field_rate`), swaps data types (e.g., booleans to strings, numbers to strings, lists to objects via `type_confusion_rate`), or replaces values with `null` (`null_injection_rate`). Can target specific keys via `target_fields` or any key when empty. Inbound requests pass through untouched.

### Module 3: Semantic Mirage (`semantic_mirage`)
- **Stage**: Response-side (`mutate_response`).
- **Mechanism**: Silently poisons relational or tabular query results without breaking JSON syntax. Injects temporal anomalies by modifying ISO-8601 timestamps so that `created_at` occurs chronologically after `last_updated`, and mutates authorization roles (e.g., rewriting `user_role` to `"admin"`). Inbound requests pass through untouched.

### Module 4: RBAC Jailbreaker (`rbac_jailbreaker`)
- **Stage**: Dual-stage (Request-side and Response-side).
- **Mechanism**: On the request path (`mutate_request`), inspects payloads and SQL statements for prohibited mutation patterns (`prohibit_mutations`) or write operations (POST, PUT, PATCH, DELETE, SQL statements) targeting `protected_fields`. Violations raise `ShortCircuitFault` returning `403 Forbidden`. On the response path (`mutate_response`), recursively inspects JSON payloads and redacts values of all configured `protected_fields` to `"[REDACTED]"`.

### Module 5: Token Trap (`token_trap`)
- **Stage**: Request-side (`mutate_request`).
- **Mechanism**: Detects requests targeting configured `trap_paths` and tracks visit depth using normalized request state signatures. For depths up to `max_cyclic_depth`, short-circuits forwarding by raising `ShortCircuitFault` with synthetic HTTP 200 responses containing circular redirection instructions pointing to the next path in `trap_paths`. When the cycle depth exceeds the threshold, requests pass through upstream. Responses pass through untouched.

### Fixed Interceptor Pipeline Order

In `ProxyService` (`app/services/proxy_service.py`), strategies execute in a strict sequence:

```
token_trap -> rbac_jailbreak -> network_chaos -> schema_mutilation -> semantic_mirage
```

Short-circuiting strategies run first to abort requests before calling upstream endpoints. Payload-mutilating strategies run last to operate on genuine upstream responses.

## Storage Architecture

Execution traces and metrics are persisted through backend-agnostic repositories:

- **Storage Engines**:
  - `aiosqlite`: Default local persistence writing to `SQLITE_PATH` (`chaos_traces.db`).
  - `asyncpg`: Activated when `DATABASE_DSN` is set, connecting to a PostgreSQL instance.
- **Asynchronous Non-Blocking Writes**:
  - `TraceRepository` uses an internal `asyncio.Queue` (capacity 10,000 items) drained by a dedicated background worker task.
  - Proxy request forwarding enqueues `TraceEntry` records without awaiting database disk I/O.
  - If the queue is full, incoming traces are dropped with a warning log rather than stalling proxy execution.
  - All database interactions use raw parameterized SQL queries with zero ORM overhead.

## Not Implemented Yet

The following files and components are intentionally empty placeholders or unbuilt:

- `app/services/dag_service.py`: Phase 3 Directed Acyclic Graph execution tracking and loop recurrence counter.
- `app/services/scoring_service.py`: Phase 3 quantitative Resiliency Score calculator.
- `app/utils/math_metrics.py`: Phase 3 mathematical resiliency formulation.
- `app/schemas/evaluation.py`: Phase 3 evaluation run request and response DTO schemas.
- `tests/test_dag_engine.py`: Phase 3 DAG state machine and cycle detection test suite.
- Container orchestration configurations and CI pipelines (Phase 4).
- Real-time web evaluation dashboard (Phase 4).
- Autonomous agent benchmarking harnesses and test datasets (Phase 5).
