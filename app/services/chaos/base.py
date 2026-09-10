from __future__ import annotations

from abc import ABC, abstractmethod

from httpx import Request, Response

from app.schemas.chaos_config import ChaosConfig


class BaseChaosStrategy(ABC):
    """Pluggable chaos mutation strategy (Strategy pattern, ARCHITECTURE.md §4A).

    `name` identifies the strategy in fault records and logs.
    `default_severity` is the calibrated severity used when a config omits one.
    """

    name: str = "base"
    default_severity: float = 0.5

    @abstractmethod
    def should_apply(self, request: Request, config: ChaosConfig) -> bool:
        """Determines stochastically or deterministically if this fault triggers."""

    @abstractmethod
    async def mutate_request(self, request: Request, config: ChaosConfig) -> Request:
        """Applies pre-forwarding mutations (header tampering, payload strip).

        May raise `ShortCircuitFault` to answer without contacting the upstream.
        """

    @abstractmethod
    async def mutate_response(self, response: Response, config: ChaosConfig) -> Response | None:
        """Applies post-forwarding mutations (semantic poisoning, error injection).

        Return `None` to signal "unchanged" — the pipeline then keeps the response
        it already had. Returning a Response replaces it.
        """
