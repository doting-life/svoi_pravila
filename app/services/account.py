"""Account lifecycle: PD consent onboarding, revoke, export and two-phase deletion.

Deterministic orchestration only. Telegram identity comes from the adapters (validated
webhook sender / Mini App initData); client-supplied user_id is never trusted.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.checkpoints import CheckpointStore
from app.integrations.telegram import TelegramWebAppUser
from app.observability.postgres_sink import anonymize_service_events_in
from app.persistence.postgres import UserModel
from app.repositories.consents import ConsentRepository, OnboardingRecord, PostgresConsentRepository
from app.repositories.postgres_users import PostgresUserRepository
from app.repositories.relationships import RelationshipRepository
from app.repositories.users import UserRecord, UserRepository

logger = logging.getLogger("svoi_pravila.account")

PENDING_ACTION_TTL = timedelta(minutes=5)


@dataclass(slots=True)
class DeleteResult:
    status: str  # "deleted" | "not_requested" | "failed"
    checkpoints_removed: int = 0
    redis_cleanup_failed: bool = False


@dataclass(slots=True)
class ExportResult:
    status: str  # "ok" | "not_requested"
    filename: str | None = None
    content: bytes | None = None


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return str(value)


class AccountService:
    def __init__(
        self,
        *,
        consents: ConsentRepository,
        users: UserRepository,
        relationships: RelationshipRepository,
        checkpoints: CheckpointStore,
        sessions: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self.consents = consents
        self.users = users
        self.relationships = relationships
        self.checkpoints = checkpoints
        self.sessions = sessions

    @property
    def _transactional(self) -> bool:
        return (
            self.sessions is not None
            and isinstance(self.consents, PostgresConsentRepository)
            and isinstance(self.users, PostgresUserRepository)
        )

    async def start(self, telegram_user_id: int) -> OnboardingRecord:
        """Idempotent: a current valid consent stays active; otherwise the user is set to consent_pending.

        Stores only the Telegram ID and onboarding state; no user profile is created before consent.
        """
        record = await self.consents.ensure_onboarding(telegram_user_id)
        if await self.is_active(telegram_user_id):
            return record
        if record.state == "consent_pending":
            return record
        return await self.consents.set_state(telegram_user_id, "consent_pending")

    async def is_active(self, telegram_user_id: int) -> bool:
        return await self.consents.has_active_consent(telegram_user_id)

    async def accept(self, telegram_user: TelegramWebAppUser) -> UserRecord:
        """Grant current PD consent and create/update the user in one transaction."""
        if self._transactional:
            assert self.sessions is not None
            consents: PostgresConsentRepository = self.consents  # type: ignore[assignment]
            users: PostgresUserRepository = self.users  # type: ignore[assignment]
            async with self.sessions() as session:
                await consents.accept_in(session, telegram_user.id)
                model = await users.get_or_create_from_telegram_in(session, telegram_user)
                await session.commit()
                await session.refresh(model)
                return users._to_record(model)
        await self.consents.accept(telegram_user.id)
        return await self.users.get_or_create_from_telegram(telegram_user)

    async def decline(self, telegram_user_id: int) -> OnboardingRecord:
        """Declining stores nothing beyond onboarding state; an active consent is left unchanged."""
        record = await self.consents.ensure_onboarding(telegram_user_id)
        if record.state == "active":
            return record
        return await self.consents.set_state(telegram_user_id, "consent_pending")

    async def revoke(self, telegram_user_id: int) -> bool:
        """Stop processing. Stored data is kept until /delete."""
        return await self.consents.revoke(telegram_user_id)

    async def request_delete(self, telegram_user_id: int) -> OnboardingRecord:
        await self.consents.ensure_onboarding(telegram_user_id)
        return await self.consents.set_pending_action(telegram_user_id, "delete_confirm", PENDING_ACTION_TTL)

    async def request_export(self, telegram_user_id: int) -> OnboardingRecord:
        await self.consents.ensure_onboarding(telegram_user_id)
        return await self.consents.set_pending_action(telegram_user_id, "export_confirm", PENDING_ACTION_TTL)

    async def cancel_pending(self, telegram_user_id: int) -> None:
        await self.consents.clear_pending_action(telegram_user_id)

    async def confirm_delete(self, telegram_user_id: int) -> DeleteResult:
        if not await self.consents.consume_pending_action(telegram_user_id, "delete_confirm"):
            return DeleteResult(status="not_requested")

        # Phase 1: revoke and commit so the consent gate blocks new work immediately.
        await self.consents.revoke(telegram_user_id)

        # Phase 2: lock user, anonymise telemetry, delete account and consent data, commit.
        try:
            user_id = await self._delete_account_data(telegram_user_id)
        except Exception as exc:
            logger.warning("account deletion failed error_type=%s", type(exc).__name__)
            return DeleteResult(status="failed")

        # Redis cleanup only after a successful PostgreSQL commit; TTL is the fallback.
        result = DeleteResult(status="deleted")
        if user_id is not None:
            try:
                result.checkpoints_removed = await self.checkpoints.delete_for_user(user_id)
            except Exception as exc:
                result.redis_cleanup_failed = True
                logger.warning("checkpoint cleanup failed error_type=%s", type(exc).__name__)
        return result

    async def _delete_account_data(self, telegram_user_id: int) -> str | None:
        if self._transactional:
            assert self.sessions is not None
            consents: PostgresConsentRepository = self.consents  # type: ignore[assignment]
            users: PostgresUserRepository = self.users  # type: ignore[assignment]
            async with self.sessions() as session:
                model = (
                    await session.execute(
                        select(UserModel)
                        .where(UserModel.telegram_user_id == telegram_user_id)
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                user_id = model.user_id if model is not None else None
                if user_id is not None:
                    await anonymize_service_events_in(session, user_id)
                    await users.delete_in(session, user_id)
                await consents.delete_for_telegram_user_in(session, telegram_user_id)
                await session.commit()
                return user_id

        user = await self.users.get_by_telegram_id(telegram_user_id)
        user_id = user.user_id if user is not None else None
        if user_id is not None:
            for relationship in await self.relationships.list_for_user(user_id):
                await self.relationships.delete(user_id, relationship.relationship_id)
            await self.users.delete(user_id)
        await self.consents.delete_for_telegram_user(telegram_user_id)
        return user_id

    async def confirm_export(self, telegram_user_id: int) -> ExportResult:
        if not await self.consents.consume_pending_action(telegram_user_id, "export_confirm"):
            return ExportResult(status="not_requested")
        payload = await self.build_export(telegram_user_id)
        content = json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default).encode("utf-8")
        return ExportResult(status="ok", filename="svoi-pravila-export.json", content=content)

    async def build_export(self, telegram_user_id: int) -> dict[str, Any]:
        onboarding = await self.consents.get_onboarding(telegram_user_id)
        consents = await self.consents.list_consents(telegram_user_id)
        user = await self.users.get_by_telegram_id(telegram_user_id)
        relationships = await self.relationships.list_for_user(user.user_id) if user is not None else []
        return {
            "exported_at": datetime.now(timezone.utc),
            "telegram_user_id": telegram_user_id,
            "onboarding": asdict(onboarding) if onboarding is not None else None,
            "consents": [asdict(consent) for consent in consents],
            "user": asdict(user) if user is not None else None,
            "relationships": [
                {
                    "relationship_id": rel.relationship_id,
                    "relation_type": rel.relation_type,
                    "aliases": list(rel.aliases),
                    "communication_style": dict(rel.communication_style),
                    "ruleset_version": rel.ruleset_version,
                    "rules": [rule.model_dump(mode="json") for rule in rel.rules],
                }
                for rel in relationships
            ],
        }
