from __future__ import annotations

import random
from typing import TYPE_CHECKING

from app.services.chaos.base import BaseChaosStrategy
from app.services.chaos.network_chaos import NetworkChaosStrategy
from app.services.chaos.rbac_jailbreak import RbacJailbreakStrategy
from app.services.chaos.schema_mutilation import SchemaMutilationStrategy
from app.services.chaos.semantic_mirage import SemanticMirageStrategy
from app.services.chaos.token_trap import TokenTrapStrategy

if TYPE_CHECKING:
    from app.schemas.chaos_config import ChaosConfig

__all__ = [
    "BaseChaosStrategy",
    "NetworkChaosStrategy",
    "RbacJailbreakStrategy",
    "SchemaMutilationStrategy",
    "SemanticMirageStrategy",
    "TokenTrapStrategy",
    "build_strategies",
]


def build_strategies(
    config: ChaosConfig | None,
    rng: random.Random | None = None,
) -> list[BaseChaosStrategy]:
    """Build and return active chaos strategies in the fixed pipeline order.

    Order: token_trap -> rbac_jailbreak -> network_chaos -> schema_mutilation -> semantic_mirage.
    Only strategies whose configuration profile exists and is enabled are instantiated.
    """
    if config is None:
        return []

    profiles = config.fuzzing_profiles
    if profiles is None:
        return []

    strategies: list[BaseChaosStrategy] = []

    if profiles.token_trap is not None and profiles.token_trap.enabled:
        strategies.append(TokenTrapStrategy(rng=rng))

    if profiles.rbac_jailbreaker is not None and profiles.rbac_jailbreaker.enabled:
        strategies.append(RbacJailbreakStrategy(rng=rng))

    if profiles.network_chaos is not None and profiles.network_chaos.enabled:
        strategies.append(NetworkChaosStrategy(rng=rng))

    if profiles.schema_mutilation is not None and profiles.schema_mutilation.enabled:
        strategies.append(SchemaMutilationStrategy(rng=rng))

    if profiles.semantic_mirage is not None and profiles.semantic_mirage.enabled:
        strategies.append(SemanticMirageStrategy(rng=rng))

    return strategies
