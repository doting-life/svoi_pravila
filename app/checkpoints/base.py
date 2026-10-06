from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CheckpointSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    workflow: str
    stage: str
    status: Literal["in_progress", "completed", "failed", "blocked"]
    attempt: int = 1
    artifact_names: list[str] = Field(default_factory=list)
    state: dict[str, Any]
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CheckpointStore(ABC):
    @abstractmethod
    async def save(self, snapshot: CheckpointSnapshot, *, user_id: str | None = None) -> None:
        raise NotImplementedError

    @abstractmethod
    async def load(self, request_id: UUID) -> CheckpointSnapshot | None:
        raise NotImplementedError

    @abstractmethod
    async def delete(self, request_id: UUID, *, user_id: str | None = None) -> None:
        raise NotImplementedError

    @abstractmethod
    async def delete_for_user(self, user_id: str) -> int:
        """Delete every checkpoint indexed for user_id. Returns the number of checkpoint keys removed."""
        raise NotImplementedError
