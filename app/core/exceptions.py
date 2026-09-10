from __future__ import annotations

import httpx


class ChaosEngineError(Exception):
    """Base class for every domain error raised by the chaos engine."""


class ConfigurationError(ChaosEngineError):
    """chaos.yaml or environment settings are missing/invalid."""


class UpstreamResolutionError(ChaosEngineError):
    """No upstream base URL could be resolved for an intercepted request."""


class UpstreamUnreachableError(ChaosEngineError):
    """The real upstream could not be contacted."""

    def __init__(self, url: str, reason: str) -> None:
        self.url = url
        self.reason = reason
        super().__init__(f"upstream {url} unreachable: {reason}")


class ShortCircuitFault(ChaosEngineError):
    """Raised by a chaos strategy to abort forwarding and return a synthetic response.

    The proxy pipeline catches this, returns `response` verbatim to the agent, and
    records the fault. This is the ONLY sanctioned way to answer without touching
    the upstream; it keeps `BaseChaosStrategy` signatures identical to ARCHITECTURE.md §4A.
    """

    def __init__(self, response: httpx.Response, fault_name: str, severity: float) -> None:
        self.response = response
        self.fault_name = fault_name
        self.severity = severity
        super().__init__(f"{fault_name} short-circuited with HTTP {response.status_code}")
