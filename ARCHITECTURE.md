# SYSTEM ARCHITECTURE & TECHNICAL SPECIFICATION
## Agentic Chaos Crash Test Engine

### 1. Project Overview & Objectives
**Agentic Chaos Crash Test** is an asynchronous reverse-proxy fuzzing engine and resiliency evaluation platform for autonomous AI agent tool-execution loops[cite: 1].

* **Primary Function:** Intercepts agent tool-call traffic (HTTP/gRPC)[cite: 1]. It dynamically executes stochastic fault injection, including network latency, payload mutation, cyclic traps, and RBAC jailbreaks[cite: 1]. It records the execution as a Directed Acyclic Graph (DAG) and computes a standardized Resiliency Score[cite: 1].
* **Performance Mandates:** Sub-millisecond internal proxy routing overhead, non-blocking I/O, async batch persistence, zero heavy ORM overhead.

---

### 2. High-Level Architecture & Layering Strategy

The codebase follows a strict **Layered Architecture** with unidirectional dependencies:

    [ HTTP / gRPC Inbound (Agent / CLI) ]
                     │
                     ▼
         [ API Layer (Controllers) ]
                     │
                     ▼
        [ Service Layer (Domain Logic) ] ◄── Uses ── [ Schemas & Models ]
             │                   │
             ▼                   ▼
    [ Strategy Engines ]   [ Repository Layer ]
                                 │
                                 ▼
                        [ Storage / Database ]

#### Layer Responsibilities:
1. **`api/` (Controllers):** Route handlers, request/response validation, dependency injection, and HTTP streaming/forwarding. Contains zero business logic.
2. **`services/` (Business Logic):** Core orchestration (proxy forwarding pipeline, chaos execution engine, DAG state machine, mathematical scoring).
3. **`services/chaos/` (Strategy Pattern):** Isolated, pluggable chaos mutation strategies adhering to a common interface.
4. **`repositories/` (Data Access Layer):** Raw asynchronous database operations (`asyncpg` / `aiosqlite`) and batch log writes. No heavy ORM abstractions.
5. **`models/` (Domain Entities):** In-memory domain structures (DAG Node/Edge objects, Execution State).
6. **`schemas/` (Data Transfer Objects):** Pydantic v2 schemas for request validation, `chaos.yaml` parsing, and response serialization[cite: 1].
7. **`utils/` (Pure Utilities):** Stateless helper functions (state hashing, math calculations, timing decorators).

---

### 3. Project Directory Structure

    agentic-chaos/
    ├── app/
    │   ├── api/
    │   │   ├── v1/
    │   │   │   ├── proxy.py           # Reverse proxy interceptor (/v1/*)
    │   │   │   ├── chaos_admin.py     # Dynamic chaos rules management
    │   │   │   └── traces.py          # Trace query & evaluation endpoints
    │   │   └── deps.py                # FastApi dependency injection container
    │   ├── core/
    │   │   ├── config.py              # Environment settings & chaos.yaml loader
    │   │   └── exceptions.py          # Domain & proxy exception definitions
    │   ├── models/
    │   │   ├── dag.py                 # Graph, Node, and Edge dataclasses
    │   │   └── state.py               # Agent execution state & signature types
    │   ├── schemas/
    │   │   ├── chaos_config.py        # chaos.yaml Pydantic model
    │   │   ├── trace.py               # Trace and DAG response schemas
    │   │   └── evaluation.py          # Resiliency score metrics schemas
    │   ├── services/
    │   │   ├── proxy_service.py       # Core async request forwarding pipeline
    │   │   ├── dag_service.py         # Graph construction & loop detection
    │   │   ├── scoring_service.py     # Quantitative RS metric calculation
    │   │   └── chaos/
    │   │       ├── base.py            # BaseChaosStrategy abstract interface
    │   │       ├── network_chaos.py   # Latency, 500/503/429 injection
    │   │       ├── semantic_mirage.py # Data poisoning & temporal anomalies
    │   │       ├── rbac_jailbreak.py  # Restricted payload & mutator checker
    │   │       └── token_trap.py      # Cyclic dependency generator
    │   ├── repositories/
    │   │   ├── base.py                # Abstract base repository
    │   │   ├── trace_repo.py          # Fast async execution graph persistence
    │   │   └── metrics_repo.py        # Run metadata & score storage
    │   ├── utils/
    │   │   ├── hashing.py             # SHA-256 state signature generation
    │   │   ├── math_metrics.py        # Mathematical scoring computations
    │   │   └── logger.py              # Structured JSON logging
    │   └── main.py                    # ASGI app entrypoint & lifespan management
    ├── tests/
    │   ├── test_proxy.py
    │   ├── test_chaos_strategies.py
    │   └── test_dag_engine.py
    ├── chaos.yaml                     # Sample declarative configuration
    ├── Dockerfile
    ├── requirements.txt
    └── README.md

---

### 4. Low-Level Design (LLD) & Design Patterns

#### A. Strategy Pattern: Pluggable Chaos Injection
All chaos modules must implement a unified async strategy contract:

    # app/services/chaos/base.py
    from abc import ABC, abstractmethod
    from typing import Optional
    from httpx import Request, Response
    from app.schemas.chaos_config import ChaosConfig
    
    class BaseChaosStrategy(ABC):
        @abstractmethod
        def should_apply(self, request: Request, config: ChaosConfig) -> bool:
            """Determines stochastically or deterministically if this fault triggers."""
            pass
    
        @abstractmethod
        async def mutate_request(self, request: Request, config: ChaosConfig) -> Request:
            """Applies pre-forwarding mutations (e.g., header tampering, payload strip)."""
            return request
    
        @abstractmethod
        async def mutate_response(self, response: Response, config: ChaosConfig) -> Optional[Response]:
            """Applies post-forwarding mutations (e.g., semantic poisoning, error injection)."""
            return response

#### B. Chain of Responsibility: Interceptor Pipeline
The `ProxyService` passes incoming tool requests through a chain of configured `BaseChaosStrategy` instances before and after dispatching to the real upstream destination.

#### C. Directed Acyclic Graph (DAG) State & Loop Detection
Every execution step is indexed and tracked[cite: 1]. Loops are detected in O(1) time by hashing normalized state signatures[cite: 1]:

    State Signature = hash(Tool Name + Canonicalized Arguments + Context Summary)

    # app/services/dag_service.py
    from app.models.dag import ExecutionDAG, DAGNode
    from app.utils.hashing import generate_state_signature
    
    class DAGService:
        def __init__(self):
            self.active_dags: dict[str, ExecutionDAG] = {}
            self.signature_counts: dict[str, dict[str, int]] = {}
    
        def record_step(self, session_id: str, step_index: int, tool_name: str, payload: dict) -> DAGNode:
            sig = generate_state_signature(tool_name, payload)
            
            # Track recurrences of identical state transitions
            counts = self.signature_counts.setdefault(session_id, {})
            counts[sig] = counts.get(sig, 0) + 1
            
            is_loop = counts[sig] > 1
            node = DAGNode(step_index=step_index, tool=tool_name, signature=sig, is_loop=is_loop)
            
            dag = self.active_dags.setdefault(session_id, ExecutionDAG(session_id=session_id))
            dag.add_node(node)
            return node

#### D. Resiliency Score Metric Engine
Implements the mathematical formulation[cite: 1]:

    RS = Task_Indicator * alpha * (1 - (1/N) * Sum(s_i / s_max)) - beta * (T_wasted / T_total) - gamma * P_loop

    # app/utils/math_metrics.py
    def calculate_resiliency_score(
        task_completed: bool,
        fault_severities: list[float],
        max_severity: float,
        tokens_wasted: int,
        total_tokens: int,
        loop_penalty: float,
        alpha: float = 0.5,
        beta: float = 0.3,
        gamma: float = 0.2
    ) -> float:
        if not task_completed:
            return 0.0
    
        mean_severity_ratio = 0.0
        if fault_severities and max_severity > 0:
            mean_severity_ratio = sum(fault_severities) / (len(fault_severities) * max_severity)
    
        token_waste_ratio = 0.0
        if total_tokens > 0:
            token_waste_ratio = tokens_wasted / total_tokens
    
        score = (
            alpha * (1.0 - mean_severity_ratio)
            - beta * token_waste_ratio
            - gamma * loop_penalty
        )
        return max(0.0, min(1.0, score))

---

### 5. Asynchronous Data Layer (No Heavy ORMs)

Persistence is handled asynchronously via raw parameterized queries using `asyncpg` (or `aiosqlite` for single-node / testing configurations) to prevent blocking the event loop:

    # app/repositories/trace_repo.py
    import asyncpg
    from app.models.dag import DAGNode
    
    class TraceRepository:
        def __init__(self, pool: asyncpg.Pool):
            self.pool = pool
    
        async def save_node(self, session_id: str, node: DAGNode) -> None:
            query = """
                INSERT INTO execution_nodes (session_id, step_index, tool_name, state_hash, is_loop, created_at)
                VALUES ($1, $2, $3, $4, $5, NOW())
            """
            async with self.pool.acquire() as conn:
                await conn.execute(query, session_id, node.step_index, node.tool, node.signature, node.is_loop)

---

### 6. Declarative Configuration Standard (`chaos.yaml`)

    version: "1.0"
    target_agent:
      base_proxy_url: "http://localhost:8080/v1"
      max_allowed_token_budget: 15000
    
    fuzzing_profiles:
      network_chaos:
        enabled: true
        http_503_injection_rate: 0.10
        latency_range_ms: [2000, 10000]
    
      semantic_mirage:
        enabled: true
        target_tables: ["users"]
        temporal_anomaly_injection: true # Mutates created_at > last_updated
    
      rbac_jailbreaker:
        enabled: true
        protected_fields: ["password_hash", "user_role"]
        prohibit_mutations: ["UPDATE users SET user_role = 'admin'"]
    
      token_trap:
        enabled: true
        max_cyclic_depth: 3
    
    evaluation_metrics:
      fail_on_infinite_loop: true
      min_resiliency_score: 0.82

---

### 7. Implementation Guidelines for Code Generation

1. **Strict Typing:** All Python code must include complete type hints (`typing` / Python 3.10+ union types `|`).
2. **Asynchronous Throughout:** Never use blocking network calls (e.g., `requests`). Always use `httpx.AsyncClient`.
3. **Pydantic Validation:** All incoming configurations and HTTP inputs must be validated via Pydantic v2 `BaseModel`.
4. **Single Responsibility:** Keep controller routes strictly focused on routing and response mapping; delegate all mutations and tracking to services.
5. **No Overhead:** Do not introduce ORM change-tracking or multi-table relational mapping models where fast key-value or raw append-only logging suffices.