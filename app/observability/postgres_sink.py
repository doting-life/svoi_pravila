from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.observability.trace import TraceEvent
from app.persistence.postgres import ServiceEventModel, UserModel

logger = logging.getLogger("svoi_pravila.observability")

MAX_BUFFERED_EVENTS_PER_REQUEST = 64
MAX_BUFFERED_REQUESTS = 10_000


def _str(value: Any, limit: int) -> str | None:
    if value is None or not isinstance(value, (str, int)):
        return None
    return str(value)[:limit]


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    return Decimal(str(value))


def event_to_row(event: TraceEvent) -> dict[str, Any]:
    """Allow-list mapping: only these technical columns are ever persisted; all other metadata is dropped."""
    meta = event.metadata
    return {
        "at": event.at,
        "request_id": event.request_id,
        "user_id": _str(event.user_id, 64),
        "relationship_id": _str(meta.get("relationship_id"), 128),
        "workflow": _str(event.workflow, 32) or "",
        "stage": _str(event.stage, 32) or "",
        "status": _str(event.status, 16) or "",
        "attempt": int(event.attempt),
        "latency_ms": int(event.latency_ms),
        "provider": _str(meta.get("provider"), 32),
        "model": _str(meta.get("model"), 64),
        "input_tokens": _int(meta.get("input_tokens")),
        "output_tokens": _int(meta.get("output_tokens")),
        "cost_usd": _decimal(meta.get("cost_usd")),
        "provider_latency_ms": _int(meta.get("provider_latency_ms")),
        "generation_ms": _int(meta.get("generation_ms")),
        "validation_ms": _int(meta.get("validation_ms")),
        "generate_attempts": _int(meta.get("generate_attempts")),
        "ruleset_version": _int(meta.get("ruleset_version")),
        "self_check_status": _str(meta.get("self_check_status"), 16),
        "error_type": _str(meta.get("error_type"), 64),
    }


async def insert_service_events_in(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    """Insert telemetry rows, serialised against account deletion. Does not commit.

    Referenced users are locked FOR KEY SHARE in the same transaction as the INSERT, so a
    concurrent deletion (which locks users FOR UPDATE before anonymising service_events) either
    waits for this insert and then anonymises it, or commits first; in that case the user row is
    gone and user_id/relationship_id are written as NULL.
    """
    user_ids = {row["user_id"] for row in rows if row.get("user_id")}
    existing: set[str] = set()
    if user_ids:
        existing = set(
            (
                await session.execute(
                    select(UserModel.user_id)
                    .where(UserModel.user_id.in_(sorted(user_ids)))
                    .with_for_update(read=True, key_share=True)
                )
            ).scalars()
        )
    for row in rows:
        if row.get("user_id") and row["user_id"] not in existing:
            row["user_id"] = None
            row["relationship_id"] = None
    await session.execute(insert(ServiceEventModel), rows)


async def anonymize_service_events_in(session: AsyncSession, user_id: str) -> int:
    """Detach telemetry from a user being deleted. Caller must already hold users FOR UPDATE. Does not commit."""
    result = await session.execute(
        update(ServiceEventModel)
        .where(ServiceEventModel.user_id == user_id)
        .values(user_id=None, relationship_id=None)
    )
    return int(result.rowcount or 0)


class PostgresTraceSink:
    """Persists privacy-safe trace rows to service_events.

    Stage events are buffered in memory per request and written in one INSERT when the
    terminal request event arrives. The write runs as a background task, so it never adds
    latency to, nor raises into, the user request.
    """

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions
        self._buffers: dict[UUID, list[dict[str, Any]]] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    async def record(self, event: TraceEvent) -> None:
        try:
            row = event_to_row(event)
            if event.stage != "request":
                if event.request_id not in self._buffers and len(self._buffers) >= MAX_BUFFERED_REQUESTS:
                    return
                buffer = self._buffers.setdefault(event.request_id, [])
                if len(buffer) < MAX_BUFFERED_EVENTS_PER_REQUEST:
                    buffer.append(row)
                return
            rows = self._buffers.pop(event.request_id, [])
            rows.append(row)
            task = asyncio.get_running_loop().create_task(self._write(rows))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        except Exception as exc:  # observability must never break the request
            logger.warning("service_events buffer failed request_id=%s error_type=%s", event.request_id, type(exc).__name__)

    async def _write(self, rows: list[dict[str, Any]]) -> None:
        try:
            async with self._sessions() as session:
                await insert_service_events_in(session, rows)
                await session.commit()
        except Exception as exc:
            logger.warning(
                "service_events write failed request_id=%s rows=%d error_type=%s",
                rows[-1]["request_id"],
                len(rows),
                type(exc).__name__,
            )

    async def drain(self) -> None:
        """Wait for in-flight writes (used on shutdown and in tests)."""
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
