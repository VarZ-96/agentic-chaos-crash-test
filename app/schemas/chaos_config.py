from pydantic import BaseModel, Field
from typing import List, Tuple, Optional

class TargetAgentConfig(BaseModel):
    base_proxy_url: str
    max_allowed_token_budget: int

class NetworkChaosConfig(BaseModel):
    enabled: bool = False
    http_503_injection_rate: float = 0.0
    latency_range_ms: Tuple[int, int] = (0, 0)

class SemanticMirageConfig(BaseModel):
    enabled: bool = False
    target_tables: List[str] = Field(default_factory=list)
    temporal_anomaly_injection: bool = False

class RbacJailbreakerConfig(BaseModel):
    enabled: bool = False
    protected_fields: List[str] = Field(default_factory=list)
    prohibit_mutations: List[str] = Field(default_factory=list)

class TokenTrapConfig(BaseModel):
    enabled: bool = False
    max_cyclic_depth: int = 0

class FuzzingProfilesConfig(BaseModel):
    network_chaos: Optional[NetworkChaosConfig] = None
    semantic_mirage: Optional[SemanticMirageConfig] = None
    rbac_jailbreaker: Optional[RbacJailbreakerConfig] = None
    token_trap: Optional[TokenTrapConfig] = None

class EvaluationMetricsConfig(BaseModel):
    fail_on_infinite_loop: bool = True
    min_resiliency_score: float = 0.0

class ChaosConfig(BaseModel):
    version: str
    target_agent: TargetAgentConfig
    fuzzing_profiles: FuzzingProfilesConfig
    evaluation_metrics: EvaluationMetricsConfig
