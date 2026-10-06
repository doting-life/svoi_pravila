from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from app.integrations.telegram import TelegramWebAppUser


@dataclass(slots=True)
class UserRecord:
    user_id: str
    telegram_user_id: int
    first_name: str
    last_name: str | None = None
    username: str | None = None
    language_code: str | None = None
    default_relationship_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class UserRepository(ABC):
    @abstractmethod
    async def get(self, user_id: str) -> UserRecord | None:
        raise NotImplementedError

    @abstractmethod
    async def get_by_telegram_id(self, telegram_user_id: int) -> UserRecord | None:
        raise NotImplementedError

    @abstractmethod
    async def get_or_create_from_telegram(self, telegram_user: TelegramWebAppUser) -> UserRecord:
        raise NotImplementedError

    @abstractmethod
    async def set_default_relationship(self, user_id: str, relationship_id: str | None) -> UserRecord:
        raise NotImplementedError

    @abstractmethod
    async def delete(self, user_id: str) -> bool:
        """Delete the user and owned relationships/rules. Returns False when the user does not exist."""
        raise NotImplementedError


class InMemoryUserRepository(UserRepository):
    def __init__(self) -> None:
        self._by_id: dict[str, UserRecord] = {}
        self._by_telegram_id: dict[int, str] = {}

    def upsert(self, record: UserRecord) -> None:
        self._by_id[record.user_id] = deepcopy(record)
        self._by_telegram_id[record.telegram_user_id] = record.user_id

    async def get(self, user_id: str) -> UserRecord | None:
        record = self._by_id.get(user_id)
        return deepcopy(record) if record else None

    async def get_by_telegram_id(self, telegram_user_id: int) -> UserRecord | None:
        user_id = self._by_telegram_id.get(telegram_user_id)
        return await self.get(user_id) if user_id else None

    async def get_or_create_from_telegram(self, telegram_user: TelegramWebAppUser) -> UserRecord:
        existing = await self.get_by_telegram_id(telegram_user.id)
        now = datetime.now(timezone.utc)
        if existing is None:
            existing = UserRecord(
                user_id=f"usr_{uuid4().hex}",
                telegram_user_id=telegram_user.id,
                first_name=telegram_user.first_name,
                last_name=telegram_user.last_name,
                username=telegram_user.username,
                language_code=telegram_user.language_code,
                created_at=now,
                updated_at=now,
            )
        else:
            existing.first_name = telegram_user.first_name
            existing.last_name = telegram_user.last_name
            existing.username = telegram_user.username
            existing.language_code = telegram_user.language_code
            existing.updated_at = now
        self.upsert(existing)
        return deepcopy(existing)

    async def set_default_relationship(self, user_id: str, relationship_id: str | None) -> UserRecord:
        record = self._by_id.get(user_id)
        if record is None:
            raise KeyError(f"Unknown user {user_id}")
        record.default_relationship_id = relationship_id
        record.updated_at = datetime.now(timezone.utc)
        return deepcopy(record)

    async def delete(self, user_id: str) -> bool:
        record = self._by_id.pop(user_id, None)
        if record is None:
            return False
        self._by_telegram_id.pop(record.telegram_user_id, None)
        return True
