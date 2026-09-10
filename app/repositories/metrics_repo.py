from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import aiosqlite
import asyncpg

from app.repositories.base import BaseRepository
from app.utils.logger import get_logger

logger = get_logger("metrics_repo")

MAX_PENDING_SESSIONS: int = 10_000


@dataclass(slots=True)
class _MetricDelta:
    fault_count: int = 0
    total_faults_severity: float = 0.0
    short_circuits: int = 0


class _SqliteMetricsBackend:
    def __init__(self, path: str) -> None:
        self._path = path
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        if self._db is None:
            self._db = await aiosqlite.connect(self._path)
            self._db.row_factory = aiosqlite.Row
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS execution_runs (
                session_id TEXT PRIMARY KEY,
                fault_count INTEGER NOT NULL,
                total_faults_severity REAL NOT NULL,
                short_circuits INTEGER NOT NULL,
                updated_at TIMESTAMP NOT NULL
            )
        """)
        await self._db.commit()

    async def upsert_batch(
        self,
        deltas: list[tuple[str, int, float, int, datetime]],
    ) -> None:
        if not self._db or not deltas:
            return
        query = """
            INSERT INTO execution_runs (session_id, fault_count, total_faults_severity, short_circuits, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                fault_count = execution_runs.fault_count + excluded.fault_count,
                total_faults_severity = execution_runs.total_faults_severity + excluded.total_faults_severity,
                short_circuits = execution_runs.short_circuits + excluded.short_circuits,
                updated_at = excluded.updated_at
        """
        params = [
            (
                s_id,
                int(fc),
                float(sev),
                int(sc),
                dt.isoformat(),
            )
            for s_id, fc, sev, sc, dt in deltas
        ]
        await self._db.executemany(query, params)
        await self._db.commit()

    async def upsert_run(
        self,
        session_id: str,
        fault_count: int,
        total_faults_severity: float,
        short_circuits: int,
        updated_at: datetime,
    ) -> None:
        await self.upsert_batch([(session_id, fault_count, total_faults_severity, short_circuits, updated_at)])

    async def select_run(self, session_id: str) -> dict[str, object] | None:
        if not self._db:
            return None
        query = """
            SELECT session_id, fault_count, total_faults_severity, short_circuits, updated_at
            FROM execution_runs
            WHERE session_id = ?
        """
        async with self._db.execute(query, (session_id,)) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return None
            return {
                "session_id": str(row["session_id"]),
                "fault_count": int(row["fault_count"]),
                "total_faults_severity": float(row["total_faults_severity"]),
                "short_circuits": int(row["short_circuits"]),
                "updated_at": row["updated_at"],
            }

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None


class _AsyncpgMetricsBackend:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def init(self) -> None:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn)
        async with self._pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS execution_runs (
                    session_id TEXT PRIMARY KEY,
                    fault_count INTEGER NOT NULL,
                    total_faults_severity REAL NOT NULL,
                    short_circuits INTEGER NOT NULL,
                    updated_at TIMESTAMP NOT NULL
                )
            """)

    async def upsert_batch(
        self,
        deltas: list[tuple[str, int, float, int, datetime]],
    ) -> None:
        if not self._pool or not deltas:
            return
        query = """
            INSERT INTO execution_runs (session_id, fault_count, total_faults_severity, short_circuits, updated_at)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (session_id) DO UPDATE SET
                fault_count = execution_runs.fault_count + EXCLUDED.fault_count,
                total_faults_severity = execution_runs.total_faults_severity + EXCLUDED.total_faults_severity,
                short_circuits = execution_runs.short_circuits + EXCLUDED.short_circuits,
                updated_at = EXCLUDED.updated_at
        """
        params = [
            (
                s_id,
                int(fc),
                float(sev),
                int(sc),
                dt.astimezone(timezone.utc).replace(tzinfo=None),
            )
            for s_id, fc, sev, sc, dt in deltas
        ]
        async with self._pool.acquire() as conn:
            await conn.executemany(query, params)

    async def upsert_run(
        self,
        session_id: str,
        fault_count: int,
        total_faults_severity: float,
        short_circuits: int,
        updated_at: datetime,
    ) -> None:
        await self.upsert_batch([(session_id, fault_count, total_faults_severity, short_circuits, updated_at)])

    async def select_run(self, session_id: str) -> dict[str, object] | None:
        if not self._pool:
            return None
        query = """
            SELECT session_id, fault_count, total_faults_severity, short_circuits, updated_at
            FROM execution_runs
            WHERE session_id = $1
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(query, session_id)
            if row is None:
                return None
            return {
                "session_id": str(row["session_id"]),
                "fault_count": int(row["fault_count"]),
                "total_faults_severity": float(row["total_faults_severity"]),
                "short_circuits": int(row["short_circuits"]),
                "updated_at": row["updated_at"],
            }

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None


class MetricsRepository(BaseRepository):
    """Asynchronous, dual-backend metrics repository with in-memory accumulation and batched flush."""

    def __init__(
        self,
        dsn: str | None = None,
        sqlite_path: str = "chaos_traces.db",
    ) -> None:
        self._dsn = dsn
        self._sqlite_path = sqlite_path

        self._backend: _SqliteMetricsBackend | _AsyncpgMetricsBackend
        if dsn:
            self._backend = _AsyncpgMetricsBackend(dsn)
        else:
            self._backend = _SqliteMetricsBackend(sqlite_path)

        self._pending: dict[str, _MetricDelta] = {}
        self._initialized = False
        self._closed = False

    async def initialize(self) -> None:
        """Idempotently initialize storage backend and tables."""
        if self._initialized and not self._closed:
            return

        await self._backend.init()
        self._pending = {}
        self._initialized = True
        self._closed = False

    async def record_run(
        self,
        session_id: str,
        fault_count: int,
        total_faults_severity: float,
        short_circuits: int,
    ) -> None:
        """Additively accumulate run metrics in memory without blocking I/O."""
        if self._closed or not self._initialized:
            logger.warning("Attempted record_run on uninitialized or closed MetricsRepository")
            return

        if len(self._pending) >= MAX_PENDING_SESSIONS and session_id not in self._pending:
            await self.flush()

        delta = self._pending.get(session_id)
        if delta is None:
            self._pending[session_id] = _MetricDelta(
                fault_count=int(fault_count),
                total_faults_severity=float(total_faults_severity),
                short_circuits=int(short_circuits),
            )
        else:
            delta.fault_count += int(fault_count)
            delta.total_faults_severity += float(total_faults_severity)
            delta.short_circuits += int(short_circuits)

    async def flush(self) -> None:
        """Flush accumulated in-memory metrics deltas to the database."""
        if not self._initialized or self._closed or not self._pending:
            return

        pending = self._pending
        self._pending = {}

        now = datetime.now(timezone.utc)
        deltas = [
            (
                s_id,
                delta.fault_count,
                delta.total_faults_severity,
                delta.short_circuits,
                now,
            )
            for s_id, delta in pending.items()
        ]
        if not deltas:
            return

        try:
            await self._backend.upsert_batch(deltas)
        except Exception:
            logger.exception("Failed to flush metrics batch to database")

    async def get_run(self, session_id: str) -> dict[str, object] | None:
        """Retrieve aggregated run metrics for a session, flushing pending deltas first."""
        if not self._initialized or self._closed:
            return None
        await self.flush()
        return await self._backend.select_run(session_id)

    async def close(self) -> None:
        """Flush pending metrics deltas and close storage backend."""
        if self._closed or not self._initialized:
            return
        try:
            await self.flush()
        except Exception:
            logger.exception("Failed to flush metrics deltas on close")
        self._closed = True
        await self._backend.close()
