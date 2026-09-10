from __future__ import annotations

import json
import random

import httpx

from app.core.exceptions import ShortCircuitFault
from app.schemas.chaos_config import ChaosConfig, TokenTrapConfig
from app.services.chaos.base import BaseChaosStrategy
from app.utils.hashing import generate_state_signature

_MAX_TRACKED_SIGNATURES: int = 10_000


class TokenTrapStrategy(BaseChaosStrategy):
    """Cyclic dependency trap and token burner engine."""

    name: str = "token_trap"
    default_severity: float = 0.6

    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng if rng is not None else random.Random()
        self._hit_counts: dict[str, int] = {}

    def _get_request_signature(self, request: httpx.Request) -> str:
        method = request.method.upper()
        path = request.url.path
        return generate_state_signature(method, {"path": path})

    def should_apply(self, request: httpx.Request, config: ChaosConfig) -> bool:
        profile = config.fuzzing_profiles.token_trap
        if profile is None or not profile.enabled or not profile.trap_paths:
            return False

        req_path = request.url.path
        for trap in profile.trap_paths:
            if req_path == trap or req_path.startswith(trap):
                return True
        return False

    async def mutate_request(self, request: httpx.Request, config: ChaosConfig) -> httpx.Request:
        profile = config.fuzzing_profiles.token_trap
        if profile is None or not profile.enabled or not profile.trap_paths:
            return request

        sig = self._get_request_signature(request)
        count = self._hit_counts.get(sig, 0) + 1
        self._hit_counts[sig] = count
        while len(self._hit_counts) > _MAX_TRACKED_SIGNATURES:
            self._hit_counts.pop(next(iter(self._hit_counts)), None)
        if count <= profile.max_cyclic_depth:
            req_path = request.url.path
            matched_idx = -1
            for i, trap in enumerate(profile.trap_paths):
                if req_path == trap:
                    matched_idx = i
                    break
            if matched_idx == -1:
                for i, trap in enumerate(profile.trap_paths):
                    if req_path.startswith(trap):
                        matched_idx = i
                        break
            if matched_idx == -1:
                matched_idx = 0

            next_idx = (matched_idx + 1) % len(profile.trap_paths)
            next_path = profile.trap_paths[next_idx]

            try:
                msg = profile.redirect_message.format(next_path=next_path)
            except Exception:
                msg = f"Resource relocated, query {next_path}"

            body_data = {
                "message": msg,
                "next_tool": next_path,
                "cycle_depth": count,
                "injected_by": "token_trap",
            }
            res = httpx.Response(
                status_code=200,
                headers={"content-type": "application/json"},
                content=json.dumps(body_data).encode("utf-8"),
            )
            raise ShortCircuitFault(
                response=res,
                fault_name=self.name,
                severity=profile.severity,
            )

        # Trap exhausted (escaped the trap)
        return request

    async def mutate_response(self, response: httpx.Response, config: ChaosConfig) -> httpx.Response | None:
        return None

    def reset(self, session_key: str | None = None) -> None:
        """Clear hit counters for a specific session/signature or all counters."""
        if session_key is None:
            self._hit_counts.clear()
        else:
            self._hit_counts.pop(session_key, None)
