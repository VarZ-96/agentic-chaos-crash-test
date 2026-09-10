from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

FaultStage = Literal["request", "response"]


@dataclass(slots=True)
class FaultRecord:
    """One injected fault, appended in injection order."""
    name: str
    severity: float
    stage: FaultStage
    detail: str


@dataclass(slots=True)
class InterceptContext:
    """Mutable per-request state threaded through the interceptor pipeline."""
    session_id: str
    method: str
    path: str
    upstream_url: str
    step_index: int = 0
    faults: list[FaultRecord] = field(default_factory=list)
    started_at: float = field(default_factory=time.perf_counter)
    short_circuited: bool = False

    def record_fault(self, name: str, severity: float, stage: FaultStage, detail: str) -> None:
        self.faults.append(FaultRecord(name=name, severity=severity, stage=stage, detail=detail))

    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self.started_at) * 1000.0
