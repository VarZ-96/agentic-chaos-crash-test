from __future__ import annotations

import json
import random
from typing import Any

from httpx import Request, Response

from app.schemas.chaos_config import ChaosConfig, SchemaMutilationConfig
from app.services.chaos.base import BaseChaosStrategy


class SchemaMutilationStrategy(BaseChaosStrategy):
    """Structural response mutation strategy.

    Corrupts JSON payloads post-forwarding by dropping fields, confusing data
    types, and injecting null values to test agent resilience against schema drift.
    """

    name: str = "schema_mutilation"
    default_severity: float = 0.5

    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng if rng is not None else random.Random()

    def should_apply(self, request: Request, config: ChaosConfig) -> bool:
        """Determines whether schema mutilation is enabled.

        Returns False if the profile is absent or disabled. Mutation dice
        are rolled inside mutate_response.
        """
        profile = config.fuzzing_profiles.schema_mutilation
        if profile is None or not profile.enabled:
            return False
        return True

    async def mutate_request(self, request: Request, config: ChaosConfig) -> Request:
        """Schema mutilation is strictly response-side; requests pass untouched."""
        return request

    def _confuse_type(self, val: Any) -> Any:
        """Transforms a value into an unexpected/wrong type to confuse schema validators."""
        if isinstance(val, bool):
            return str(val)
        if isinstance(val, (int, float)):
            return str(val)
        if isinstance(val, str):
            try:
                return int(val)
            except ValueError:
                return 0
        if isinstance(val, list):
            return len(val)
        if isinstance(val, dict):
            return "[object]"
        if val is None:
            return "null"
        return str(val)

    def _mutate_dict(
        self,
        d: dict[str, Any],
        depth: int,
        target_fields: set[str],
        profile: SchemaMutilationConfig,
    ) -> bool:
        if depth > 5:
            return False

        mutated = False
        for key in list(d.keys()):
            is_eligible = (not target_fields) or (key in target_fields)
            key_mutated = False

            if is_eligible:
                # 1. drop_field_rate — delete a key outright
                if profile.drop_field_rate > 0.0 and self._rng.random() < profile.drop_field_rate:
                    del d[key]
                    mutated = True
                    key_mutated = True
                    continue

                # 2. type_confusion_rate — replace a value with a wrong-typed one
                if profile.type_confusion_rate > 0.0 and self._rng.random() < profile.type_confusion_rate:
                    d[key] = self._confuse_type(d[key])
                    mutated = True
                    key_mutated = True
                    continue

                # 3. null_injection_rate — replace a value with None
                if profile.null_injection_rate > 0.0 and self._rng.random() < profile.null_injection_rate:
                    d[key] = None
                    mutated = True
                    key_mutated = True
                    continue

            # If this key was not mutated at this level, recurse into nested dicts/lists
            if not key_mutated:
                val = d[key]
                if isinstance(val, dict):
                    if self._mutate_dict(val, depth + 1, target_fields, profile):
                        mutated = True
                elif isinstance(val, list):
                    if self._mutate_list(val, depth + 1, target_fields, profile):
                        mutated = True

        return mutated

    def _mutate_list(
        self,
        lst: list[Any],
        depth: int,
        target_fields: set[str],
        profile: SchemaMutilationConfig,
    ) -> bool:
        if depth > 5:
            return False

        mutated = False
        for item in lst:
            if isinstance(item, dict):
                if self._mutate_dict(item, depth + 1, target_fields, profile):
                    mutated = True
            elif isinstance(item, list):
                if self._mutate_list(item, depth + 1, target_fields, profile):
                    mutated = True

        return mutated

    async def mutate_response(self, response: Response, config: ChaosConfig) -> Response | None:
        """Applies schema corruption mutations to a JSON response body.

        Returns None if:
        - The profile is missing or disabled.
        - The Content-Type is explicitly non-JSON.
        - The body cannot be decoded as UTF-8 / parsed as JSON.
        - No mutations fired (preserving the original response and recording no fault).

        Otherwise returns a new httpx.Response with original status, original headers
        MINUS content-length and content-encoding, and the re-serialized mutated JSON.
        """
        profile = config.fuzzing_profiles.schema_mutilation
        if profile is None or not profile.enabled:
            return None

        # Content-type check: if header is present, it must be JSON-ish
        content_type = response.headers.get("content-type")
        if content_type is not None and "json" not in content_type.lower():
            return None

        try:
            raw_bytes = response.content
            text = raw_bytes.decode("utf-8")
            data = json.loads(text)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

        target_fields = set(profile.target_fields)
        mutated = False

        if isinstance(data, dict):
            mutated = self._mutate_dict(data, depth=1, target_fields=target_fields, profile=profile)
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    if self._mutate_dict(item, depth=1, target_fields=target_fields, profile=profile):
                        mutated = True
                elif isinstance(item, list):
                    if self._mutate_list(item, depth=1, target_fields=target_fields, profile=profile):
                        mutated = True

        if not mutated:
            return None

        new_body = json.dumps(data).encode("utf-8")
        new_headers = {
            k: v for k, v in response.headers.items()
            if k.lower() not in ("content-length", "content-encoding")
        }
        if "content-type" not in [k.lower() for k in new_headers]:
            new_headers["content-type"] = "application/json"

        mutated_response = Response(
            status_code=response.status_code,
            headers=new_headers,
            content=new_body,
        )
        mutated_response.headers.pop("content-length", None)
        mutated_response.headers.pop("content-encoding", None)
        return mutated_response
