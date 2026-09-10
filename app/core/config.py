from __future__ import annotations

import os
import yaml
from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.exceptions import ConfigurationError, UpstreamResolutionError
from app.schemas.chaos_config import ChaosConfig


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ENVIRONMENT: str = "development"
    CHAOS_CONFIG_PATH: str = "chaos.yaml"
    DATABASE_DSN: str | None = None
    SQLITE_PATH: str = "chaos_traces.db"
    UPSTREAM_BASE_URL: str | None = None
    PROXY_TIMEOUT_SECONDS: float = 30.0
    LOG_LEVEL: str = "INFO"
    chaos_config: ChaosConfig | None = None

    def load_chaos_config(self) -> None:
        if not os.path.exists(self.CHAOS_CONFIG_PATH):
            self.chaos_config = None
            return

        try:
            with open(self.CHAOS_CONFIG_PATH, "r", encoding="utf-8") as f:
                raw_config = yaml.safe_load(f)
        except Exception as exc:
            raise ConfigurationError(
                f"Failed to parse chaos configuration at '{self.CHAOS_CONFIG_PATH}': {exc}"
            ) from exc

        if raw_config is None:
            self.chaos_config = None
            return

        try:
            self.chaos_config = ChaosConfig(**raw_config)
        except ValidationError as exc:
            raise ConfigurationError(
                f"Validation error in chaos configuration at '{self.CHAOS_CONFIG_PATH}': {exc}"
            ) from exc
        except Exception as exc:
            raise ConfigurationError(
                f"Unexpected error loading chaos configuration at '{self.CHAOS_CONFIG_PATH}': {exc}"
            ) from exc

    def resolve_upstream(self, header_value: str | None = None) -> str:
        url: str | None = None
        if header_value and header_value.strip():
            url = header_value.strip()
        elif self.UPSTREAM_BASE_URL and self.UPSTREAM_BASE_URL.strip():
            url = self.UPSTREAM_BASE_URL.strip()
        elif self.chaos_config and self.chaos_config.target_agent.upstream_base_url:
            candidate = self.chaos_config.target_agent.upstream_base_url.strip()
            if candidate:
                url = candidate

        if not url:
            raise UpstreamResolutionError("No upstream base URL could be resolved for intercepted request")

        return url.rstrip("/")


settings = Settings()
settings.load_chaos_config()
