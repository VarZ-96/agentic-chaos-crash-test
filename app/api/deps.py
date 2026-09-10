from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from fastapi import HTTPException, status

from app.schemas.chaos_config import ChaosConfig

if TYPE_CHECKING:
    from app.services.proxy_service import ProxyService
    from app.repositories.trace_repo import TraceRepository
    from app.repositories.metrics_repo import MetricsRepository


@dataclass
class _RuntimeContainer:
    proxy_service: ProxyService | None = None
    trace_repo: TraceRepository | None = None
    metrics_repo: MetricsRepository | None = None
    chaos_config: ChaosConfig | None = None


_runtime = _RuntimeContainer()


def set_runtime(
    *,
    proxy_service: ProxyService,
    trace_repo: TraceRepository,
    metrics_repo: MetricsRepository,
    chaos_config: ChaosConfig | None = None,
) -> None:
    """Set global runtime service singletons for dependency injection."""
    _runtime.proxy_service = proxy_service
    _runtime.trace_repo = trace_repo
    _runtime.metrics_repo = metrics_repo
    _runtime.chaos_config = chaos_config


def clear_runtime() -> None:
    """Clear all runtime singletons (used during shutdown / testing)."""
    _runtime.proxy_service = None
    _runtime.trace_repo = None
    _runtime.metrics_repo = None
    _runtime.chaos_config = None


def set_chaos_config(config: ChaosConfig | None) -> None:
    """Hot-swap the active chaos configuration."""
    _runtime.chaos_config = config


def get_chaos_config_optional() -> ChaosConfig | None:
    """Retrieve the active chaos configuration if set, or None without raising 503."""
    return _runtime.chaos_config


def get_proxy_service() -> ProxyService:
    """FastAPI dependency for ProxyService. Raises 503 if uninitialized."""
    if _runtime.proxy_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Proxy service is not initialized",
        )
    return _runtime.proxy_service


def get_trace_repository() -> TraceRepository:
    """FastAPI dependency for TraceRepository. Raises 503 if uninitialized."""
    if _runtime.trace_repo is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Trace repository is not initialized",
        )
    return _runtime.trace_repo


def get_metrics_repository() -> MetricsRepository:
    """FastAPI dependency for MetricsRepository. Raises 503 if uninitialized."""
    if _runtime.metrics_repo is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Metrics repository is not initialized",
        )
    return _runtime.metrics_repo


def get_chaos_config() -> ChaosConfig:
    """FastAPI dependency for active ChaosConfig. Raises 503 if uninitialized."""
    if _runtime.chaos_config is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Chaos configuration is not initialized",
        )
    return _runtime.chaos_config
