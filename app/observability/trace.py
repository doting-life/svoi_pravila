from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import UUID


@dataclass(slots=True)
class TraceEvent:
    request_id: UUID
    workflow: str
    stage: str
    status: str
    attempt: int
    latency_ms: int
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)
    # Internal user id only (never Telegram identity); used for DAU aggregation.
    user_id: str | None = None


class TraceSink(Protocol):
    async def record(self, event: TraceEvent) -> None: ...


class RequestTraceSink:
    """Technical trace only. Raw messages/prompts are intentionally excluded."""

    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    async def record(self, event: TraceEvent) -> None:
        safe = {
            key: value
            for key, value in event.metadata.items()
            if key not in {"text", "prompt", "raw_prompt", "raw_output"}
        }
        event.metadata = safe
        self.events.append(event)
