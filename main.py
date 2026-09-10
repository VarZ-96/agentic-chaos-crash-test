from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
import httpx

from app.api import deps
from app.api.v1.chaos_admin import router as chaos_admin_router
from app.api.v1.proxy import router as proxy_router
from app.api.v1.traces import router as traces_router
from app.core.config import settings
from app.core.exceptions import UpstreamResolutionError
from app.repositories.metrics_repo import MetricsRepository
from app.repositories.trace_repo import TraceRepository
from app.services.chaos import build_strategies
from app.services.proxy_service import ProxyService
from app.utils.logger import configure_logging, get_logger

logger = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # a. Configure structured logging
    configure_logging(settings.LOG_LEVEL)
    logger.info("Agentic Chaos Crash Test Engine starting up...")

    # b. Construct repositories and initialize storage backends
    trace_repo = TraceRepository(
        dsn=settings.DATABASE_DSN,
        sqlite_path=settings.SQLITE_PATH,
    )
    metrics_repo = MetricsRepository(
        dsn=settings.DATABASE_DSN,
        sqlite_path=settings.SQLITE_PATH,
    )
    await trace_repo.initialize()
    await metrics_repo.initialize()

    # c. Build shared httpx AsyncClient
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(settings.PROXY_TIMEOUT_SECONDS),
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        follow_redirects=False,
    )

    # d. Build active chaos strategies in fixed pipeline order
    strategies = build_strategies(settings.chaos_config)

    # e. Construct ProxyService and configure active chaos profile
    proxy_service = ProxyService(
        client=client,
        strategies=strategies,
        trace_repo=trace_repo,
        metrics_repo=metrics_repo,
    )
    proxy_service.set_config(settings.chaos_config)

    # f. Publish runtime singletons to dependency injection container
    deps.set_runtime(
        proxy_service=proxy_service,
        trace_repo=trace_repo,
        metrics_repo=metrics_repo,
        chaos_config=settings.chaos_config,
    )

    # g. Log structured startup line
    try:
        resolved_upstream: str | None = settings.resolve_upstream(None)
    except UpstreamResolutionError as exc:
        resolved_upstream = None
        logger.warning(
            "Default upstream base URL could not be resolved at startup: %s",
            exc,
        )

    storage_backend = "postgres" if settings.DATABASE_DSN else "sqlite"
    strategy_names = [s.name for s in strategies]
    logger.info(
        "Agentic Chaos Engine started. Upstream: %s, strategies: %s, storage: %s",
        resolved_upstream,
        strategy_names,
        storage_backend,
        extra={
            "context": {
                "upstream": resolved_upstream,
                "strategies": strategy_names,
                "storage_backend": storage_backend,
            }
        },
    )

    # h. Yield control to application, then teardown in reverse
    try:
        yield
    finally:
        logger.info("Agentic Chaos Crash Test Engine shutting down...")
        # Reverse teardown: deps -> client -> metrics_repo -> trace_repo
        # Teardown is exception-safe so one failure does not strand the others.
        try:
            deps.clear_runtime()
        except Exception as exc:
            logger.error("Error clearing runtime dependencies: %s", exc, exc_info=True)

        try:
            await client.aclose()
        except Exception as exc:
            logger.error("Error closing HTTP client: %s", exc, exc_info=True)

        try:
            await metrics_repo.close()
        except Exception as exc:
            logger.error("Error closing metrics repository: %s", exc, exc_info=True)

        try:
            await trace_repo.close()
        except Exception as exc:
            logger.error("Error closing trace repository: %s", exc, exc_info=True)


app = FastAPI(
    title="Agentic Chaos Crash Test Engine",
    description="Asynchronous reverse-proxy fuzzing engine and resiliency evaluation platform.",
    version="1.0.0",
    lifespan=lifespan,
)

# Route registration ordering:
# Specific v1 routers (traces and chaos_admin) MUST be registered before the proxy
# router at prefix "/v1". The proxy router defines a catch-all endpoint "/{path:path}"
# which would swallow "/v1/traces" and "/v1/chaos/*" if registered first.
app.include_router(traces_router, prefix="/v1")
app.include_router(chaos_admin_router, prefix="/v1")
app.include_router(proxy_router, prefix="/v1")


@app.get("/health")
async def health_check() -> dict[str, Any]:
    config = deps.get_chaos_config_optional()
    if config is None:
        config = settings.chaos_config
    chaos_config_loaded = config is not None
    strategies = [s.name for s in build_strategies(config)] if config is not None else []
    return {
        "status": "ok",
        "chaos_config_loaded": chaos_config_loaded,
        "strategies": strategies,
    }
