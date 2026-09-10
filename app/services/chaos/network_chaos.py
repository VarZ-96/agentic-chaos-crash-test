from __future__ import annotations

import asyncio
import json
import random

from httpx import Request, Response

from app.core.exceptions import ShortCircuitFault
from app.schemas.chaos_config import ChaosConfig
from app.services.chaos.base import BaseChaosStrategy


class NetworkChaosStrategy(BaseChaosStrategy):
    """Network-level chaos simulation strategy.

    Injects transport-level latency and synthetic HTTP errors (503, 500, 429)
    before requests are forwarded to the upstream agent or tool service.
    """

    name: str = "network_chaos"
    default_severity: float = 0.4

    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng if rng is not None else random.Random()

    def should_apply(self, request: Request, config: ChaosConfig) -> bool:
        """Determines whether network chaos is active.

        Returns False if the profile is missing or disabled. The individual
        rates for latency and error injection are evaluated probabilistically
        inside mutate_request so that the interceptor pipeline always consults
        an active profile.
        """
        profile = config.fuzzing_profiles.network_chaos
        if profile is None or not profile.enabled:
            return False
        return True

    async def mutate_request(self, request: Request, config: ChaosConfig) -> Request:
        """Applies network latency and potential HTTP error short-circuits.

        Latency is evaluated first using asyncio.sleep. Error injections are
        checked in the fixed priority order: 503, 500, 429 (first hit wins).
        When an error triggers, a ShortCircuitFault is raised with a synthetic
        httpx.Response so upstream forwarding is aborted.
        """
        profile = config.fuzzing_profiles.network_chaos
        if profile is None or not profile.enabled:
            return request

        # 1. Latency injection
        if profile.latency_injection_rate > 0.0 and self._rng.random() < profile.latency_injection_rate:
            low, high = profile.latency_range_ms
            delay_s = self._rng.uniform(low, high) / 1000.0
            if delay_s > 0.0:
                await asyncio.sleep(delay_s)

        # 2. Error injection (fixed order: 503, 500, 429 - first hit wins)
        if profile.http_503_injection_rate > 0.0 and self._rng.random() < profile.http_503_injection_rate:
            payload = json.dumps({
                "error": "Service Unavailable",
                "injected_by": self.name,
            }).encode("utf-8")
            resp = Response(
                status_code=503,
                headers={"content-type": "application/json"},
                content=payload,
            )
            raise ShortCircuitFault(response=resp, fault_name=self.name, severity=profile.severity)

        if profile.http_500_injection_rate > 0.0 and self._rng.random() < profile.http_500_injection_rate:
            payload = json.dumps({
                "error": "Internal Server Error",
                "injected_by": self.name,
            }).encode("utf-8")
            resp = Response(
                status_code=500,
                headers={"content-type": "application/json"},
                content=payload,
            )
            raise ShortCircuitFault(response=resp, fault_name=self.name, severity=profile.severity)

        if profile.http_429_injection_rate > 0.0 and self._rng.random() < profile.http_429_injection_rate:
            retry_after = self._rng.randint(1, 60)
            payload = json.dumps({
                "error": "Too Many Requests",
                "injected_by": self.name,
            }).encode("utf-8")
            resp = Response(
                status_code=429,
                headers={
                    "content-type": "application/json",
                    "retry-after": str(retry_after),
                },
                content=payload,
            )
            raise ShortCircuitFault(response=resp, fault_name=self.name, severity=profile.severity)

        return request

    async def mutate_response(self, response: Response, config: ChaosConfig) -> Response | None:
        """Network chaos operates strictly at the transport / request stage.

        Response-side mutations are handled by downstream strategies in the
        pipeline (e.g., schema mutilation and semantic mirage). Always returns None
        to signal that the response was unmodified.
        """
        return None
