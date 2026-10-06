from __future__ import annotations

from copy import deepcopy
from uuid import UUID

from app.checkpoints.base import CheckpointSnapshot, CheckpointStore


class InMemoryCheckpointStore(CheckpointStore):
    def __init__(self) -> None:
        self._items: dict[UUID, CheckpointSnapshot] = {}
        self._by_user: dict[str, set[UUID]] = {}

    async def save(self, snapshot: CheckpointSnapshot, *, user_id: str | None = None) -> None:
        self._items[snapshot.request_id] = deepcopy(snapshot)
        if user_id:
            self._by_user.setdefault(user_id, set()).add(snapshot.request_id)

    async def load(self, request_id: UUID) -> CheckpointSnapshot | None:
        value = self._items.get(request_id)
        return deepcopy(value) if value else None

    async def delete(self, request_id: UUID, *, user_id: str | None = None) -> None:
        self._items.pop(request_id, None)
        if user_id and user_id in self._by_user:
            self._by_user[user_id].discard(request_id)

    async def delete_for_user(self, user_id: str) -> int:
        removed = 0
        for request_id in self._by_user.pop(user_id, set()):
            if self._items.pop(request_id, None) is not None:
                removed += 1
        return removed
