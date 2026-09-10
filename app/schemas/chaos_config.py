from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TargetAgentConfig(BaseModel):
    base_proxy_url: str
    max_allowed_token_budget: int
    upstream_base_url: str | None = None


class NetworkChaosConfig(BaseModel):
    enabled: bool = False
    severity: float = Field(default=0.4, ge=0.0, le=1.0)
    http_503_injection_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    http_500_injection_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    http_429_injection_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    latency_injection_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    latency_range_ms: tuple[int, int] = (0, 0)

    @model_validator(mode="after")
    def validate_latency_range(self) -> NetworkChaosConfig:
        low, high = self.latency_range_ms
        if low < 0:
            raise ValueError(f"latency_range_ms lower bound must be >= 0, got {low}")
        if high < low:
            raise ValueError(f"latency_range_ms upper bound ({high}) must be >= lower bound ({low})")
        return self


class SchemaMutilationConfig(BaseModel):
    enabled: bool = False
    severity: float = Field(default=0.5, ge=0.0, le=1.0)
    drop_field_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    type_confusion_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    null_injection_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    target_fields: list[str] = Field(default_factory=list)


class SemanticMirageConfig(BaseModel):
    enabled: bool = False
    severity: float = Field(default=0.7, ge=0.0, le=1.0)
    target_tables: list[str] = Field(default_factory=list)
    temporal_anomaly_injection: bool = False
    corruption_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    role_field: str = "user_role"
    role_mutation_value: str | None = None
    created_at_field: str = "created_at"
    updated_at_field: str = "last_updated"


class RbacJailbreakerConfig(BaseModel):
    enabled: bool = False
    severity: float = Field(default=0.9, ge=0.0, le=1.0)
    protected_fields: list[str] = Field(default_factory=list)
    prohibit_mutations: list[str] = Field(default_factory=list)


class TokenTrapConfig(BaseModel):
    enabled: bool = False
    severity: float = Field(default=0.6, ge=0.0, le=1.0)
    max_cyclic_depth: int = 0
    trap_paths: list[str] = Field(default_factory=list)
    redirect_message: str = "Resource relocated, query {next_path}"


class FuzzingProfilesConfig(BaseModel):
    network_chaos: NetworkChaosConfig | None = None
    schema_mutilation: SchemaMutilationConfig | None = None
    semantic_mirage: SemanticMirageConfig | None = None
    rbac_jailbreaker: RbacJailbreakerConfig | None = None
    token_trap: TokenTrapConfig | None = None


class EvaluationMetricsConfig(BaseModel):
    fail_on_infinite_loop: bool = True
    min_resiliency_score: float = 0.0


class ChaosConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    target_agent: TargetAgentConfig
    fuzzing_profiles: FuzzingProfilesConfig
    evaluation_metrics: EvaluationMetricsConfig
