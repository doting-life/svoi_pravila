from __future__ import annotations

from typing import Any
from uuid import UUID

from app.checkpoints.base import CheckpointSnapshot, CheckpointStore


class RedisCheckpointStore(CheckpointStore):
    """Redis-backed transient workflow checkpoint store.

    The concrete redis client is intentionally duck-typed so importing the core
    does not require the optional Redis runtime unless this adapter is selected.
    """

    def __init__(self, redis: Any, ttl_seconds: int = 1200) -> None:
        self.redis = redis
        self.ttl_seconds = ttl_seconds

    def _key(self, request_id: UUID) -> str:
        return f"request:{request_id}"

    @staticmethod
    def _user_index_key(user_id: str) -> str:
        return f"user_checkpoints:{user_id}"

    async def save(self, snapshot: CheckpointSnapshot, *, user_id: str | None = None) -> None:
        key = self._key(snapshot.request_id)
        if not user_id:
            await self.redis.set(key, snapshot.model_dump_json(), ex=self.ttl_seconds)
            return
        index_key = self._user_index_key(user_id)
        pipe = self.redis.pipeline(transaction=True)
        pipe.set(key, snapshot.model_dump_json(), ex=self.ttl_seconds)
        pipe.sadd(index_key, key)
        pipe.expire(index_key, self.ttl_seconds)
        await pipe.execute()

    async def load(self, request_id: UUID) -> CheckpointSnapshot | None:
        raw = await self.redis.get(self._key(request_id))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return CheckpointSnapshot.model_validate_json(raw)

    async def delete(self, request_id: UUID, *, user_id: str | None = None) -> None:
        key = self._key(request_id)
        if not user_id:
            await self.redis.delete(key)
            return
        pipe = self.redis.pipeline(transaction=True)
        pipe.delete(key)
        pipe.srem(self._user_index_key(user_id), key)
        await pipe.execute()

    async def delete_for_user(self, user_id: str) -> int:
        index_key = self._user_index_key(user_id)
        members = await self.redis.smembers(index_key) or set()
        keys = [m.decode("utf-8") if isinstance(m, bytes) else str(m) for m in members]
        removed = 0
        for key in keys:
            removed += int(await self.redis.delete(key) or 0)
        await self.redis.delete(index_key)
        return removed
