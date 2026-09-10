from __future__ import annotations

from datetime import datetime, timedelta
import json
import random
from typing import Any

import httpx

from app.schemas.chaos_config import ChaosConfig, SemanticMirageConfig
from app.services.chaos.base import BaseChaosStrategy


def _parse_iso_datetime(value: str) -> datetime | None:
    """Parse ISO-8601 string, tolerating trailing 'Z'."""
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if normalized.endswith("Z") or normalized.endswith("z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(normalized)
    except (ValueError, TypeError):
        return None


class SemanticMirageStrategy(BaseChaosStrategy):
    """Silent row corruption engine for PostgreSQL-style query payloads."""

    name: str = "semantic_mirage"
    default_severity: float = 0.7

    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng if rng is not None else random.Random()

    def should_apply(self, request: httpx.Request, config: ChaosConfig) -> bool:
        profile = config.fuzzing_profiles.semantic_mirage
        if profile is None or not profile.enabled:
            return False
        return True

    async def mutate_request(self, request: httpx.Request, config: ChaosConfig) -> httpx.Request:
        return request

    async def mutate_response(self, response: httpx.Response, config: ChaosConfig) -> httpx.Response | None:
        profile = config.fuzzing_profiles.semantic_mirage
        if profile is None or not profile.enabled:
            return None

        content_type = response.headers.get("content-type", "")
        # Non-JSON content type check: if present and doesn't contain json, return None
        # But if missing or application/json, try parsing JSON
        if content_type and "application/json" not in content_type and "+json" not in content_type:
            return None

        try:
            data = response.json()
        except (ValueError, json.JSONDecodeError):
            return None

        # Collect candidate row dicts
        candidate_rows: list[dict[str, Any]] = []
        if profile.target_tables:
            if isinstance(data, dict):
                for table in profile.target_tables:
                    val = data.get(table)
                    if isinstance(val, dict):
                        candidate_rows.append(val)
                    elif isinstance(val, list):
                        for item in val:
                            if isinstance(item, dict):
                                candidate_rows.append(item)
        else:
            if isinstance(data, dict):
                candidate_rows.append(data)
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        candidate_rows.append(item)

        if not candidate_rows:
            return None

        changed = False
        for row in candidate_rows:
            if profile.corruption_rate <= 0.0:
                continue
            if profile.corruption_rate < 1.0 and self._rng.random() >= profile.corruption_rate:
                continue

            # Temporal anomaly injection
            if profile.temporal_anomaly_injection:
                created_key = profile.created_at_field
                updated_key = profile.updated_at_field
                if created_key in row and updated_key in row:
                    created_dt = _parse_iso_datetime(row[created_key])
                    updated_dt = _parse_iso_datetime(row[updated_key])
                    if created_dt is not None and updated_dt is not None:
                        # Make updated_dt strictly earlier than created_dt (random 1-72 hours earlier)
                        offset_hours = self._rng.randint(1, 72)
                        offset_seconds = self._rng.randint(0, 3599)
                        earlier_dt = created_dt - timedelta(hours=offset_hours, seconds=offset_seconds)
                        # Re-serialize in ISO format
                        # If original had timezone offset or Z, isoformat preserves tzinfo if present
                        row[updated_key] = earlier_dt.isoformat()
                        changed = True

            # Role mutation
            if profile.role_mutation_value is not None and profile.role_field in row:
                row[profile.role_field] = profile.role_mutation_value
                changed = True

        if not changed:
            return None

        new_content = json.dumps(data).encode("utf-8")
        headers = {
            k: v
            for k, v in response.headers.items()
            if k.lower() not in ("content-length", "content-encoding")
        }
        # Ensure content-type is json if missing
        if "content-type" not in [k.lower() for k in headers]:
            headers["content-type"] = "application/json"

        return httpx.Response(
            status_code=response.status_code,
            headers=headers,
            content=new_content,
        )
