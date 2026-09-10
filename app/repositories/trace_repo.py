from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import aiosqlite
import asyncpg
from pydantic import TypeAdapter

from app.repositories.base import BaseRepository
from app.schemas.trace import FaultLog, RequestLog, ResponseLog, TraceEntry
from app.utils.logger import get_logger

logger = get_logger("trace_repo")

_faults_adapter = TypeAdapter(list[FaultLog])
_SENTINEL = object()


def _parse_trace_row(
    row_id: int,
    session_id: str,
    step_index: int,
    created_at: Any,
    request_json: str,
    response_json: str,
    faults_json: str,
    short_circuited: Any,
) -> TraceEntry:
    if isinstance(created_at, str):
        parsed_dt = datetime.fromisoformat(created_at)
        if parsed_dt.tzinfo is None:
            parsed_dt = parsed_dt.replace(tzinfo=timezone.utc)
    elif isinstance(created_at, datetime):
        if created_at.tzinfo is None:
            parsed_dt = created_at.replace(tzinfo=timezone.utc)
        else:
            parsed_dt = created_at
    else:
        parsed_dt = datetime.now(timezone.utc)

    return TraceEntry(
        id=int(row_id),
        session_id=session_id,
        step_index=int(step_index),
        created_at=parsed_dt,
        request=RequestLog.model_validate_json(request_json),
        response=ResponseLog.model_validate_json(response_json),
        faults=_faults_adapter.validate_json(faults_json),
        short_circuited=bool(short_circuited),
    )


class _SqliteTraceBackend:
    def __init__(self, path: str) -> None:
        self._path = path
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        if self._db is None:
            self._db = await aiosqlite.connect(self._path)
            self._db.row_factory = aiosqlite.Row
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS execution_traces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                step_index INTEGER NOT NULL,
                created_at TIMESTAMP NOT NULL,
                request_json TEXT NOT NULL,
                response_json TEXT NOT NULL,
                faults_json TEXT NOT NULL,
                short_circuited INTEGER NOT NULL
            )
        """)
        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_traces_session ON execution_traces(session_id)
        """)
        await self._db.commit()

    async def insert_batch(self, batch: list[TraceEntry]) -> None:
        if not self._db or not batch:
            return
        rows = [
            (
                entry.session_id,
                entry.step_index,
                entry.created_at.isoformat(),
                entry.request.model_dump_json(),
                entry.response.model_dump_json(),
                _faults_adapter.dump_json(entry.faults).decode("utf-8"),
                1 if entry.short_circuited else 0,
            )
            for entry in batch
        ]
        await self._db.executemany(
            """
            INSERT INTO execution_traces (
                session_id, step_index, created_at, request_json, response_json, faults_json, short_circuited
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        await self._db.commit()

    async def select_traces(self, session_id: str | None, limit: int) -> list[TraceEntry]:
        if not self._db:
            return []
        if session_id is not None:
            query = """
                SELECT id, session_id, step_index, created_at, request_json, response_json, faults_json, short_circuited
                FROM execution_traces
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
            """
            params: tuple[Any, ...] = (session_id, limit)
        else:
            query = """
                SELECT id, session_id, step_index, created_at, request_json, response_json, faults_json, short_circuited
                FROM execution_traces
                ORDER BY id DESC
                LIMIT ?
            """
            params = (limit,)

        async with self._db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [
                _parse_trace_row(
                    row["id"],
                    row["session_id"],
                    row["step_index"],
                    row["created_at"],
                    row["request_json"],
                    row["response_json"],
                    row["faults_json"],
                    row["short_circuited"],
                )
                for row in rows
            ]

    async def count_traces(self, session_id: str | None) -> int:
        if not self._db:
            return 0
        if session_id is not None:
            query = "SELECT COUNT(*) FROM execution_traces WHERE session_id = ?"
            params: tuple[Any, ...] = (session_id,)
        else:
            query = "SELECT COUNT(*) FROM execution_traces"
            params = ()

        async with self._db.execute(query, params) as cursor:
            row = await cursor.fetchone()
            return int(row[0]) if row else 0

    async def delete_traces(self, session_id: str | None) -> int:
        if not self._db:
            return 0
        if session_id is not None:
            query = "DELETE FROM execution_traces WHERE session_id = ?"
            params: tuple[Any, ...] = (session_id,)
        else:
            query = "DELETE FROM execution_traces"
            params = ()

        cursor = await self._db.execute(query, params)
        await self._db.commit()
        return int(cursor.rowcount)

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None


class _AsyncpgTraceBackend:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def init(self) -> None:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn)
        async with self._pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS execution_traces (
                    id BIGSERIAL PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    step_index INTEGER NOT NULL,
                    created_at TIMESTAMP NOT NULL,
                    request_json TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    faults_json TEXT NOT NULL,
                    short_circuited BOOLEAN NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_traces_session ON execution_traces(session_id);
            """)

    async def insert_batch(self, batch: list[TraceEntry]) -> None:
        if not self._pool or not batch:
            return
        rows = [
            (
                entry.session_id,
                entry.step_index,
                entry.created_at.astimezone(timezone.utc).replace(tzinfo=None),
                entry.request.model_dump_json(),
                entry.response.model_dump_json(),
                _faults_adapter.dump_json(entry.faults).decode("utf-8"),
                bool(entry.short_circuited),
            )
            for entry in batch
        ]
        async with self._pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO execution_traces (
                    session_id, step_index, created_at, request_json, response_json, faults_json, short_circuited
                ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                rows,
            )

    async def select_traces(self, session_id: str | None, limit: int) -> list[TraceEntry]:
        if not self._pool:
            return []
        if session_id is not None:
            query = """
                SELECT id, session_id, step_index, created_at, request_json, response_json, faults_json, short_circuited
                FROM execution_traces
                WHERE session_id = $1
                ORDER BY id DESC
                LIMIT $2
            """
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(query, session_id, limit)
        else:
            query = """
                SELECT id, session_id, step_index, created_at, request_json, response_json, faults_json, short_circuited
                FROM execution_traces
                ORDER BY id DESC
                LIMIT $1
            """
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(query, limit)

        return [
            _parse_trace_row(
                row["id"],
                row["session_id"],
                row["step_index"],
                row["created_at"],
                row["request_json"],
                row["response_json"],
                row["faults_json"],
                row["short_circuited"],
            )
            for row in rows
        ]

    async def count_traces(self, session_id: str | None) -> int:
        if not self._pool:
            return 0
        if session_id is not None:
            query = "SELECT COUNT(*) FROM execution_traces WHERE session_id = $1"
            async with self._pool.acquire() as conn:
                val = await conn.fetchval(query, session_id)
        else:
            query = "SELECT COUNT(*) FROM execution_traces"
            async with self._pool.acquire() as conn:
                val = await conn.fetchval(query)
        return int(val or 0)

    async def delete_traces(self, session_id: str | None) -> int:
        if not self._pool:
            return 0
        if session_id is not None:
            query = "DELETE FROM execution_traces WHERE session_id = $1"
            async with self._pool.acquire() as conn:
                status = await conn.execute(query, session_id)
        else:
            query = "DELETE FROM execution_traces"
            async with self._pool.acquire() as conn:
                status = await conn.execute(query)

        try:
            return int(status.split()[-1])
        except (IndexError, ValueError):
            return 0

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None


class TraceRepository(BaseRepository):
    """Asynchronous, dual-backend trace repository with batched background writes."""

    def __init__(
        self,
        dsn: str | None = None,
        sqlite_path: str = "chaos_traces.db",
        max_queue: int = 1000,
    ) -> None:
        self._dsn = dsn
        self._sqlite_path = sqlite_path
        self._max_queue = max_queue

        self._backend: _SqliteTraceBackend | _AsyncpgTraceBackend
        if dsn:
            self._backend = _AsyncpgTraceBackend(dsn)
        else:
            self._backend = _SqliteTraceBackend(sqlite_path)

        self._queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=max_queue)
        self._drain_task: asyncio.Task[None] | None = None
        self._initialized = False
        self._closed = False

    async def initialize(self) -> None:
        """Idempotently initialize storage backend, tables, indexes, and background drain task."""
        if self._initialized and not self._closed:
            return

        await self._backend.init()

        if self._drain_task is None or self._drain_task.done():
            self._queue = asyncio.Queue(maxsize=self._max_queue)
            self._drain_task = asyncio.create_task(self._drain_worker())

        self._initialized = True
        self._closed = False

    async def _drain_worker(self) -> None:
        """Coalescing background worker consuming up to ~64 items in single batch transactions."""
        while True:
            try:
                item = await self._queue.get()
            except asyncio.CancelledError:
                break

            if item is _SENTINEL:
                self._queue.task_done()
                break

            batch: list[TraceEntry] = [item]
            sentinel_seen = False
            while len(batch) < 64:
                try:
                    next_item = self._queue.get_nowait()
                    if next_item is _SENTINEL:
                        self._queue.task_done()
                        sentinel_seen = True
                        break
                    batch.append(next_item)
                except asyncio.QueueEmpty:
                    break

            if batch:
                try:
                    await self._backend.insert_batch(batch)
                except Exception:
                    logger.exception(
                        "Failed to write trace batch to database",
                        extra={"context": {"batch_size": len(batch)}},
                    )
                finally:
                    for _ in batch:
                        self._queue.task_done()

            if sentinel_seen:
                break

    async def save_trace(self, entry: TraceEntry) -> None:
        """Enqueue a trace entry for batched asynchronous persistence.

        Non-blocking: enqueues the entry and returns immediately.
        The write is enqueued and the row id is assigned asynchronously by the background
        drain worker, so callers must not expect a row ID return value.
        On queue overflow (asyncio.QueueFull), logs a WARNING and drops the entry without
        blocking or raising.
        """
        if self._closed:
            logger.warning("Attempted save_trace on closed TraceRepository; dropping entry")
            return

        try:
            self._queue.put_nowait(entry)
        except asyncio.QueueFull:
            logger.warning(
                "TraceRepository queue full (size=%d, max=%d); dropping trace entry",
                self._queue.qsize(),
                self._max_queue,
                extra={"context": {"session_id": entry.session_id, "step_index": entry.step_index}},
            )

    async def flush(self) -> None:
        """Wait until all currently queued trace entries have been persisted."""
        await self._queue.join()

    async def list_traces(self, session_id: str | None = None, limit: int = 100) -> list[TraceEntry]:
        """List persisted traces ordered by id DESC, flushing pending writes first."""
        await self.flush()
        return await self._backend.select_traces(session_id, limit)

    async def count_traces(self, session_id: str | None = None) -> int:
        """Count persisted traces, optionally filtered by session_id, flushing pending writes first."""
        await self.flush()
        return await self._backend.count_traces(session_id)

    async def purge(self, session_id: str | None = None) -> int:
        """Purge persisted traces, optionally filtered by session_id, flushing pending writes first."""
        await self.flush()
        return await self._backend.delete_traces(session_id)

    async def close(self) -> None:
        """Signal shutdown, wait for background drain task to finish, flush remnants, and close backend."""
        if self._closed or not self._initialized:
            return
        self._closed = True

        if self._drain_task is not None and not self._drain_task.done():
            try:
                self._queue.put_nowait(_SENTINEL)
            except asyncio.QueueFull:
                try:
                    await asyncio.wait_for(self._queue.put(_SENTINEL), timeout=5.0)
                except (asyncio.TimeoutError, Exception):
                    pass
            try:
                await asyncio.wait_for(self._drain_task, timeout=10.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                if not self._drain_task.done():
                    self._drain_task.cancel()
                    try:
                        await self._drain_task
                    except asyncio.CancelledError:
                        pass

        # Flush any remaining items
        remaining: list[TraceEntry] = []
        while not self._queue.empty():
            try:
                rem = self._queue.get_nowait()
                if rem is not _SENTINEL and isinstance(rem, TraceEntry):
                    remaining.append(rem)
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break

        if remaining:
            try:
                await self._backend.insert_batch(remaining)
            except Exception:
                logger.exception("Failed to flush remaining traces on close")

        await self._backend.close()
