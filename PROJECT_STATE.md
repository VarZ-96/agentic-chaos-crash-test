# PROJECT STATE & AGENT CONTEXT
**Last Updated:** [15-08-2026]

## 1. Current Phase
* We are currently at the beginning of **Phase 1: Core Proxy Infrastructure**.
* The project skeleton and foundational configuration have been initialized. 

## 2. Completed Milestones
* [x] Generate the foundational directory structure (api, core, models, schemas, services, repositories, utils).
* [x] Create `requirements.txt` with base async dependencies (FastAPI, httpx, pydantic, asyncpg).
* [x] Write the `main.py` entrypoint.
* [x] Write `app/core/config.py` to parse environment variables and the `chaos.yaml` Pydantic schema.

## 3. Active Work in Progress (WIP)
* Preparing to implement the core asynchronous request forwarding pipeline (`app/services/proxy_service.py`).
* Preparing to implement the directed acyclic graph (DAG) state machine tracking.

## 4. Strict Reminders for AI
* **DO NOT** use synchronous libraries for network I/O (e.g., `requests`). Always use `httpx.AsyncClient`.
* **DO NOT** introduce heavy ORMs like SQLAlchemy. Stick to raw parameterized queries with `asyncpg`.
* **DO NOT** write monolithic functions. Enforce a strict separation of concerns between Controllers (routing), Services (logic), and Repositories (data).
* Review `ARCHITECTURE.md` before making any major structural changes.