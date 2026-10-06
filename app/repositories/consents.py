"""Consent and pre-consent onboarding persistence.

Pre-consent rows contain only the Telegram user ID and onboarding state; no profile data.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.consent_texts import CONSENT_KIND_PD_PROCESSING, CONSENT_TEXT_SHA256, CONSENT_VERSION
from app.persistence.postgres import TelegramOnboardingModel, UserConsentModel

OnboardingState = Literal["consent_pending", "active", "revoked"]
PendingAction = Literal["delete_confirm", "export_confirm"]

STATE_CONSENT_PENDING: OnboardingState = "consent_pending"
STATE_ACTIVE: OnboardingState = "active"
STATE_REVOKED: OnboardingState = "revoked"


@dataclass(slots=True)
class OnboardingRecord:
    telegram_user_id: int
    state: OnboardingState
    pending_action: PendingAction | None = None
    pending_action_expires_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(slots=True)
class ConsentRecord:
    id: int
    telegram_user_id: int
    kind: str
    version: str
    text_sha256: str
    granted_at: datetime
    revoked_at: datetime | None = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class ConsentRepository(ABC):
    @abstractmethod
    async def get_onboarding(self, telegram_user_id: int) -> OnboardingRecord | None:
        raise NotImplementedError

    @abstractmethod
    async def ensure_onboarding(self, telegram_user_id: int) -> OnboardingRecord:
        """Create a consent_pending row if missing (idempotent, race-safe) and return the current row."""
        raise NotImplementedError

    @abstractmethod
    async def set_state(self, telegram_user_id: int, state: OnboardingState) -> OnboardingRecord:
        raise NotImplementedError

    @abstractmethod
    async def set_pending_action(
        self, telegram_user_id: int, action: PendingAction, ttl: timedelta
    ) -> OnboardingRecord:
        raise NotImplementedError

    @abstractmethod
    async def consume_pending_action(
        self, telegram_user_id: int, action: PendingAction, *, now: datetime | None = None
    ) -> bool:
        """Atomically clear the pending action and return True only if it matched and had not expired."""
        raise NotImplementedError

    @abstractmethod
    async def clear_pending_action(self, telegram_user_id: int) -> None:
        raise NotImplementedError

    @abstractmethod
    async def accept(
        self,
        telegram_user_id: int,
        *,
        kind: str = CONSENT_KIND_PD_PROCESSING,
        version: str = CONSENT_VERSION,
        text_sha256: str = CONSENT_TEXT_SHA256,
    ) -> ConsentRecord:
        """Idempotently grant consent and set state=active. Returns the active consent row."""
        raise NotImplementedError

    @abstractmethod
    async def revoke(self, telegram_user_id: int, *, kind: str = CONSENT_KIND_PD_PROCESSING) -> bool:
        """Revoke the active consent (if any) and set state=revoked. Returns True if a consent was revoked."""
        raise NotImplementedError

    @abstractmethod
    async def has_active_consent(
        self,
        telegram_user_id: int,
        *,
        kind: str = CONSENT_KIND_PD_PROCESSING,
        version: str = CONSENT_VERSION,
        text_sha256: str = CONSENT_TEXT_SHA256,
    ) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def list_consents(self, telegram_user_id: int) -> list[ConsentRecord]:
        raise NotImplementedError

    @abstractmethod
    async def delete_for_telegram_user(self, telegram_user_id: int) -> None:
        raise NotImplementedError


class InMemoryConsentRepository(ConsentRepository):
    def __init__(self) -> None:
        self._onboarding: dict[int, OnboardingRecord] = {}
        self._consents: list[ConsentRecord] = []
        self._next_id = 1
        self._locks: dict[int, asyncio.Lock] = {}

    def _lock(self, telegram_user_id: int) -> asyncio.Lock:
        return self._locks.setdefault(telegram_user_id, asyncio.Lock())

    def _ensure(self, telegram_user_id: int) -> OnboardingRecord:
        record = self._onboarding.get(telegram_user_id)
        if record is None:
            record = OnboardingRecord(telegram_user_id, STATE_CONSENT_PENDING, updated_at=_utcnow())
            self._onboarding[telegram_user_id] = record
        return record

    def _active(self, telegram_user_id: int, kind: str) -> ConsentRecord | None:
        for consent in self._consents:
            if consent.telegram_user_id == telegram_user_id and consent.kind == kind and consent.revoked_at is None:
                return consent
        return None

    async def get_onboarding(self, telegram_user_id: int) -> OnboardingRecord | None:
        record = self._onboarding.get(telegram_user_id)
        return deepcopy(record) if record else None

    async def ensure_onboarding(self, telegram_user_id: int) -> OnboardingRecord:
        return deepcopy(self._ensure(telegram_user_id))

    async def set_state(self, telegram_user_id: int, state: OnboardingState) -> OnboardingRecord:
        async with self._lock(telegram_user_id):
            record = self._ensure(telegram_user_id)
            record.state = state
            record.updated_at = _utcnow()
            return deepcopy(record)

    async def set_pending_action(
        self, telegram_user_id: int, action: PendingAction, ttl: timedelta
    ) -> OnboardingRecord:
        async with self._lock(telegram_user_id):
            record = self._ensure(telegram_user_id)
            record.pending_action = action
            record.pending_action_expires_at = _utcnow() + ttl
            record.updated_at = _utcnow()
            return deepcopy(record)

    async def consume_pending_action(
        self, telegram_user_id: int, action: PendingAction, *, now: datetime | None = None
    ) -> bool:
        async with self._lock(telegram_user_id):
            record = self._onboarding.get(telegram_user_id)
            if record is None or record.pending_action is None:
                return False
            matched = (
                record.pending_action == action
                and record.pending_action_expires_at is not None
                and record.pending_action_expires_at > (now or _utcnow())
            )
            record.pending_action = None
            record.pending_action_expires_at = None
            record.updated_at = _utcnow()
            return matched

    async def clear_pending_action(self, telegram_user_id: int) -> None:
        async with self._lock(telegram_user_id):
            record = self._onboarding.get(telegram_user_id)
            if record is not None:
                record.pending_action = None
                record.pending_action_expires_at = None
                record.updated_at = _utcnow()

    async def accept(
        self,
        telegram_user_id: int,
        *,
        kind: str = CONSENT_KIND_PD_PROCESSING,
        version: str = CONSENT_VERSION,
        text_sha256: str = CONSENT_TEXT_SHA256,
    ) -> ConsentRecord:
        async with self._lock(telegram_user_id):
            record = self._ensure(telegram_user_id)
            now = _utcnow()
            active = self._active(telegram_user_id, kind)
            if active is not None and (active.version != version or active.text_sha256 != text_sha256):
                active.revoked_at = now
                active = None
            if active is None:
                active = ConsentRecord(
                    id=self._next_id,
                    telegram_user_id=telegram_user_id,
                    kind=kind,
                    version=version,
                    text_sha256=text_sha256,
                    granted_at=now,
                )
                self._next_id += 1
                self._consents.append(active)
            record.state = STATE_ACTIVE
            record.pending_action = None
            record.pending_action_expires_at = None
            record.updated_at = now
            return deepcopy(active)

    async def revoke(self, telegram_user_id: int, *, kind: str = CONSENT_KIND_PD_PROCESSING) -> bool:
        async with self._lock(telegram_user_id):
            record = self._ensure(telegram_user_id)
            active = self._active(telegram_user_id, kind)
            now = _utcnow()
            if active is not None:
                active.revoked_at = now
            record.state = STATE_REVOKED
            record.pending_action = None
            record.pending_action_expires_at = None
            record.updated_at = now
            return active is not None

    async def has_active_consent(
        self,
        telegram_user_id: int,
        *,
        kind: str = CONSENT_KIND_PD_PROCESSING,
        version: str = CONSENT_VERSION,
        text_sha256: str = CONSENT_TEXT_SHA256,
    ) -> bool:
        record = self._onboarding.get(telegram_user_id)
        if record is None or record.state != STATE_ACTIVE:
            return False
        active = self._active(telegram_user_id, kind)
        return active is not None and active.version == version and active.text_sha256 == text_sha256

    async def list_consents(self, telegram_user_id: int) -> list[ConsentRecord]:
        return [
            deepcopy(consent)
            for consent in sorted(self._consents, key=lambda c: c.id)
            if consent.telegram_user_id == telegram_user_id
        ]

    async def delete_for_telegram_user(self, telegram_user_id: int) -> None:
        async with self._lock(telegram_user_id):
            self._onboarding.pop(telegram_user_id, None)
            self._consents = [c for c in self._consents if c.telegram_user_id != telegram_user_id]


class PostgresConsentRepository(ConsentRepository):
    """SQLAlchemy implementation. Row locks are effective on PostgreSQL; SQLite (tests) ignores FOR UPDATE."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    @staticmethod
    def _onboarding_record(model: TelegramOnboardingModel) -> OnboardingRecord:
        return OnboardingRecord(
            telegram_user_id=model.telegram_user_id,
            state=model.state,  # type: ignore[arg-type]
            pending_action=model.pending_action,  # type: ignore[arg-type]
            pending_action_expires_at=_aware(model.pending_action_expires_at),
            updated_at=_aware(model.updated_at),
        )

    @staticmethod
    def _consent_record(model: UserConsentModel) -> ConsentRecord:
        return ConsentRecord(
            id=model.id,
            telegram_user_id=model.telegram_user_id,
            kind=model.kind,
            version=model.version,
            text_sha256=model.text_sha256,
            granted_at=_aware(model.granted_at),  # type: ignore[arg-type]
            revoked_at=_aware(model.revoked_at),
        )

    async def lock_onboarding_in(self, session: AsyncSession, telegram_user_id: int) -> TelegramOnboardingModel:
        """INSERT ... ON CONFLICT DO NOTHING, then SELECT ... FOR UPDATE. Does not commit."""
        dialect = session.bind.dialect.name if session.bind is not None else "postgresql"
        insert_fn = sqlite_insert if dialect == "sqlite" else pg_insert
        await session.execute(
            insert_fn(TelegramOnboardingModel)
            .values(telegram_user_id=telegram_user_id, state=STATE_CONSENT_PENDING, updated_at=_utcnow())
            .on_conflict_do_nothing(index_elements=[TelegramOnboardingModel.telegram_user_id])
        )
        return (
            await session.execute(
                select(TelegramOnboardingModel)
                .where(TelegramOnboardingModel.telegram_user_id == telegram_user_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one()

    @staticmethod
    async def _active_in(session: AsyncSession, telegram_user_id: int, kind: str) -> UserConsentModel | None:
        return (
            await session.execute(
                select(UserConsentModel).where(
                    UserConsentModel.telegram_user_id == telegram_user_id,
                    UserConsentModel.kind == kind,
                    UserConsentModel.revoked_at.is_(None),
                )
            )
        ).scalar_one_or_none()

    async def accept_in(
        self,
        session: AsyncSession,
        telegram_user_id: int,
        *,
        kind: str = CONSENT_KIND_PD_PROCESSING,
        version: str = CONSENT_VERSION,
        text_sha256: str = CONSENT_TEXT_SHA256,
    ) -> UserConsentModel:
        """Grant consent inside the caller's session/transaction. Does not commit.

        The onboarding row lock serialises concurrent accepts for the same Telegram user, so the
        partial unique index ux_user_consents_active is only a backstop. After the write, the active
        row is re-read and verified against the requested version and text SHA-256.
        """
        onboarding = await self.lock_onboarding_in(session, telegram_user_id)
        now = _utcnow()
        active = await self._active_in(session, telegram_user_id, kind)
        if active is not None and (active.version != version or active.text_sha256 != text_sha256):
            active.revoked_at = now
            await session.flush()
            active = None
        if active is None:
            session.add(
                UserConsentModel(
                    telegram_user_id=telegram_user_id,
                    kind=kind,
                    version=version,
                    text_sha256=text_sha256,
                    granted_at=now,
                )
            )
            await session.flush()
        onboarding.state = STATE_ACTIVE
        onboarding.pending_action = None
        onboarding.pending_action_expires_at = None
        onboarding.updated_at = now
        await session.flush()
        verified = await self._active_in(session, telegram_user_id, kind)
        if verified is None or verified.version != version or verified.text_sha256 != text_sha256:
            raise RuntimeError("Consent grant verification failed")
        return verified

    async def get_onboarding(self, telegram_user_id: int) -> OnboardingRecord | None:
        async with self.sessions() as session:
            model = await session.get(TelegramOnboardingModel, telegram_user_id)
            return self._onboarding_record(model) if model else None

    async def ensure_onboarding(self, telegram_user_id: int) -> OnboardingRecord:
        async with self.sessions() as session:
            model = await self.lock_onboarding_in(session, telegram_user_id)
            record = self._onboarding_record(model)
            await session.commit()
            return record

    async def set_state(self, telegram_user_id: int, state: OnboardingState) -> OnboardingRecord:
        async with self.sessions() as session:
            model = await self.lock_onboarding_in(session, telegram_user_id)
            model.state = state
            model.updated_at = _utcnow()
            await session.flush()
            record = self._onboarding_record(model)
            await session.commit()
            return record

    async def set_pending_action(
        self, telegram_user_id: int, action: PendingAction, ttl: timedelta
    ) -> OnboardingRecord:
        async with self.sessions() as session:
            model = await self.lock_onboarding_in(session, telegram_user_id)
            now = _utcnow()
            model.pending_action = action
            model.pending_action_expires_at = now + ttl
            model.updated_at = now
            await session.flush()
            record = self._onboarding_record(model)
            await session.commit()
            return record

    async def consume_pending_action(
        self, telegram_user_id: int, action: PendingAction, *, now: datetime | None = None
    ) -> bool:
        async with self.sessions() as session:
            model = (
                await session.execute(
                    select(TelegramOnboardingModel)
                    .where(TelegramOnboardingModel.telegram_user_id == telegram_user_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if model is None or model.pending_action is None:
                return False
            expires_at = _aware(model.pending_action_expires_at)
            matched = (
                model.pending_action == action
                and expires_at is not None
                and expires_at > (now or _utcnow())
            )
            model.pending_action = None
            model.pending_action_expires_at = None
            model.updated_at = _utcnow()
            await session.commit()
            return matched

    async def clear_pending_action(self, telegram_user_id: int) -> None:
        async with self.sessions() as session:
            await session.execute(
                update(TelegramOnboardingModel)
                .where(TelegramOnboardingModel.telegram_user_id == telegram_user_id)
                .values(pending_action=None, pending_action_expires_at=None, updated_at=_utcnow())
            )
            await session.commit()

    async def accept(
        self,
        telegram_user_id: int,
        *,
        kind: str = CONSENT_KIND_PD_PROCESSING,
        version: str = CONSENT_VERSION,
        text_sha256: str = CONSENT_TEXT_SHA256,
    ) -> ConsentRecord:
        async with self.sessions() as session:
            model = await self.accept_in(
                session, telegram_user_id, kind=kind, version=version, text_sha256=text_sha256
            )
            record = self._consent_record(model)
            await session.commit()
            return record

    async def revoke(self, telegram_user_id: int, *, kind: str = CONSENT_KIND_PD_PROCESSING) -> bool:
        async with self.sessions() as session:
            onboarding = await self.lock_onboarding_in(session, telegram_user_id)
            now = _utcnow()
            active = await self._active_in(session, telegram_user_id, kind)
            if active is not None:
                active.revoked_at = now
            onboarding.state = STATE_REVOKED
            onboarding.pending_action = None
            onboarding.pending_action_expires_at = None
            onboarding.updated_at = now
            await session.commit()
            return active is not None

    async def has_active_consent(
        self,
        telegram_user_id: int,
        *,
        kind: str = CONSENT_KIND_PD_PROCESSING,
        version: str = CONSENT_VERSION,
        text_sha256: str = CONSENT_TEXT_SHA256,
    ) -> bool:
        async with self.sessions() as session:
            row = (
                await session.execute(
                    select(UserConsentModel.id)
                    .join(
                        TelegramOnboardingModel,
                        TelegramOnboardingModel.telegram_user_id == UserConsentModel.telegram_user_id,
                    )
                    .where(
                        TelegramOnboardingModel.telegram_user_id == telegram_user_id,
                        TelegramOnboardingModel.state == STATE_ACTIVE,
                        UserConsentModel.kind == kind,
                        UserConsentModel.version == version,
                        UserConsentModel.text_sha256 == text_sha256,
                        UserConsentModel.revoked_at.is_(None),
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            return row is not None

    async def list_consents(self, telegram_user_id: int) -> list[ConsentRecord]:
        async with self.sessions() as session:
            models = (
                await session.execute(
                    select(UserConsentModel)
                    .where(UserConsentModel.telegram_user_id == telegram_user_id)
                    .order_by(UserConsentModel.id)
                )
            ).scalars()
            return [self._consent_record(model) for model in models]

    async def delete_for_telegram_user(self, telegram_user_id: int) -> None:
        async with self.sessions() as session:
            await self.delete_for_telegram_user_in(session, telegram_user_id)
            await session.commit()

    async def delete_for_telegram_user_in(self, session: AsyncSession, telegram_user_id: int) -> None:
        await session.execute(delete(UserConsentModel).where(UserConsentModel.telegram_user_id == telegram_user_id))
        await session.execute(
            delete(TelegramOnboardingModel).where(TelegramOnboardingModel.telegram_user_id == telegram_user_id)
        )

