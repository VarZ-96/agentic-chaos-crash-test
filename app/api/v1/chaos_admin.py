from __future__ import annotations

from typing import TYPE_CHECKING
from fastapi import APIRouter, Depends, HTTPException, status

from app.api import deps
from app.core.config import settings
from app.core.exceptions import ConfigurationError
from app.schemas.chaos_config import ChaosConfig

if TYPE_CHECKING:
    from app.services.proxy_service import ProxyService

router = APIRouter(prefix="/chaos", tags=["chaos-admin"])


@router.get("/config", response_model=ChaosConfig)
async def get_active_config() -> ChaosConfig:
    """Retrieve the currently active chaos configuration."""
    config = deps.get_chaos_config_optional()
    if config is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No chaos configuration is currently active",
        )
    return config


@router.put("/config", response_model=ChaosConfig)
async def update_config(
    new_config: ChaosConfig,
    proxy_service: ProxyService = Depends(deps.get_proxy_service),
) -> ChaosConfig:
    """Hot-swap the active chaos configuration in-memory."""
    deps.set_chaos_config(new_config)
    proxy_service.set_config(new_config)
    return new_config


@router.post("/reload", response_model=ChaosConfig | None)
async def reload_config(
    proxy_service: ProxyService = Depends(deps.get_proxy_service),
) -> ChaosConfig | None:
    """Reload chaos configuration from disk (chaos.yaml) and update runtime."""
    try:
        settings.load_chaos_config()
    except ConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    config = settings.chaos_config
    deps.set_chaos_config(config)
    proxy_service.set_config(config)
    return config
