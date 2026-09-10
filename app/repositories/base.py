from __future__ import annotations

from abc import ABC, abstractmethod


class BaseRepository(ABC):
    """Abstract base repository for persistence layers."""

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize connection pools, database tables, and background tasks."""

    @abstractmethod
    async def close(self) -> None:
        """Close connections and flush pending background work."""
