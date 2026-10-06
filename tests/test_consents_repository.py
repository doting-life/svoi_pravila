from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.consent_texts import CONSENT_TEXT, CONSENT_TEXT_SHA256, CONSENT_VERSION, text_sha256
from app.integrations.telegram import TelegramWebAppUser
from app.persistence import (
    RelationshipModel,
    UserModel,
    build_async_engine,
    build_session_factory,
    create_schema,
)
from app.repositories import InMemoryConsentRepository, InMemoryUserRepository, PostgresConsentRepository
from app.repositories.postgres_users import PostgresUserRepository

TG_ID = 424242


@pytest_asyncio.fixture
async def sessions():
    engine = build_async_engine("sqlite+aiosqlite:///:memory:")
    await create_schema(engine)
    try:
        yield build_session_factory(engine)
    finally:
        await engine.dispose()


@pytest.fixture(params=["memory", "sql"])
def repo_factory(request, sessions):
    if request.param == "memory":
        return InMemoryConsentRepository()
    return PostgresConsentRepository(sessions)


def tg_user(user_id: int = TG_ID) -> TelegramWebAppUser:
    return TelegramWebAppUser(id=user_id, first_name="Anna", username="anna", language_code="ru")


def test_text_sha256_is_deterministic_and_normalized():
    assert text_sha256() == CONSENT_TEXT_SHA256 == text_sha256(CONSENT_TEXT)
    assert len(CONSENT_TEXT_SHA256) == 64
    assert text_sha256(CONSENT_TEXT.replace("\n", "\r\n") + "  \n") == CONSENT_TEXT_SHA256
    assert text_sha256(CONSENT_TEXT + " changed") != CONSENT_TEXT_SHA256
    assert CONSENT_VERSION


@pytest.mark.asyncio
async def test_ensure_onboarding_is_idempotent_and_pending(repo_factory):
    repo = repo_factory
    first = await repo.ensure_onboarding(TG_ID)
    second = await repo.ensure_onboarding(TG_ID)
    assert first.state == second.state == "consent_pending"
    assert not await repo.has_active_consent(TG_ID)


@pytest.mark.asyncio
async def test_accept_is_idempotent(repo_factory):
    repo = repo_factory
    first = await repo.accept(TG_ID)
    second = await repo.accept(TG_ID)
    assert first.id == second.id
    assert await repo.has_active_consent(TG_ID)
    assert (await repo.get_onboarding(TG_ID)).state == "active"
    assert len(await repo.list_consents(TG_ID)) == 1


@pytest.mark.asyncio
async def test_concurrent_accept_yields_single_active_consent():
    repo = InMemoryConsentRepository()
    results = await asyncio.gather(*(repo.accept(TG_ID) for _ in range(10)))
    assert len({r.id for r in results}) == 1
    assert len([c for c in await repo.list_consents(TG_ID) if c.revoked_at is None]) == 1


@pytest.mark.asyncio
async def test_has_active_consent_checks_version_and_sha(repo_factory):
    repo = repo_factory
    await repo.accept(TG_ID, version="old", text_sha256="0" * 64)
    assert not await repo.has_active_consent(TG_ID)
    assert await repo.has_active_consent(TG_ID, version="old", text_sha256="0" * 64)
    assert not await repo.has_active_consent(TG_ID, version="old")

    await repo.accept(TG_ID)
    consents = await repo.list_consents(TG_ID)
    assert len(consents) == 2
    assert consents[0].revoked_at is not None and consents[1].revoked_at is None
    assert await repo.has_active_consent(TG_ID)


@pytest.mark.asyncio
async def test_has_active_consent_requires_active_state(repo_factory):
    repo = repo_factory
    await repo.accept(TG_ID)
    await repo.set_state(TG_ID, "consent_pending")
    assert not await repo.has_active_consent(TG_ID)


@pytest.mark.asyncio
async def test_revoke_keeps_history_and_blocks(repo_factory):
    repo = repo_factory
    await repo.accept(TG_ID)
    assert await repo.revoke(TG_ID) is True
    assert await repo.revoke(TG_ID) is False
    assert not await repo.has_active_consent(TG_ID)
    assert (await repo.get_onboarding(TG_ID)).state == "revoked"
    consents = await repo.list_consents(TG_ID)
    assert len(consents) == 1 and consents[0].revoked_at is not None

    await repo.accept(TG_ID)
    assert await repo.has_active_consent(TG_ID)
    assert len(await repo.list_consents(TG_ID)) == 2


@pytest.mark.asyncio
async def test_pending_action_consume_and_expiry(repo_factory):
    repo = repo_factory
    await repo.accept(TG_ID)
    await repo.set_pending_action(TG_ID, "delete_confirm", timedelta(minutes=5))
    assert not await repo.consume_pending_action(TG_ID, "export_confirm")
    assert not await repo.consume_pending_action(TG_ID, "delete_confirm")

    await repo.set_pending_action(TG_ID, "export_confirm", timedelta(minutes=5))
    assert await repo.consume_pending_action(TG_ID, "export_confirm")
    assert not await repo.consume_pending_action(TG_ID, "export_confirm")

    await repo.set_pending_action(TG_ID, "delete_confirm", timedelta(minutes=5))
    later = datetime.now(timezone.utc) + timedelta(minutes=6)
    assert not await repo.consume_pending_action(TG_ID, "delete_confirm", now=later)

    await repo.set_pending_action(TG_ID, "delete_confirm", timedelta(minutes=5))
    await repo.clear_pending_action(TG_ID)
    assert (await repo.get_onboarding(TG_ID)).pending_action is None


@pytest.mark.asyncio
async def test_delete_for_telegram_user(repo_factory):
    repo = repo_factory
    await repo.accept(TG_ID)
    await repo.accept(TG_ID + 1)
    await repo.delete_for_telegram_user(TG_ID)
    assert await repo.get_onboarding(TG_ID) is None
    assert await repo.list_consents(TG_ID) == []
    assert await repo.has_active_consent(TG_ID + 1)


@pytest.mark.asyncio
async def test_accept_and_user_creation_share_one_transaction(sessions):
    consents = PostgresConsentRepository(sessions)
    users = PostgresUserRepository(sessions)
    async with sessions() as session:
        await consents.accept_in(session, TG_ID)
        await users.get_or_create_from_telegram_in(session, tg_user())
        await session.rollback()
    assert await consents.get_onboarding(TG_ID) is None
    assert await users.get_by_telegram_id(TG_ID) is None

    async with sessions() as session:
        await consents.accept_in(session, TG_ID)
        model = await users.get_or_create_from_telegram_in(session, tg_user())
        await session.commit()
    assert await consents.has_active_consent(TG_ID)
    assert (await users.get_by_telegram_id(TG_ID)).user_id == model.user_id


@pytest.mark.asyncio
async def test_postgres_user_get_or_create_preserved_and_delete(sessions):
    users = PostgresUserRepository(sessions)
    first = await users.get_or_create_from_telegram(tg_user())
    second = await users.get_or_create_from_telegram(tg_user())
    assert first.user_id == second.user_id
    async with sessions() as session:
        session.add(RelationshipModel(relationship_id="rel_1", user_id=first.user_id, aliases=[], communication_style={}))
        await session.commit()

    assert await users.delete(first.user_id) is True
    assert await users.delete(first.user_id) is False
    assert await users.get(first.user_id) is None
    async with sessions() as session:
        assert (await session.execute(select(UserModel))).first() is None


@pytest.mark.asyncio
async def test_in_memory_user_delete():
    users = InMemoryUserRepository()
    record = await users.get_or_create_from_telegram(tg_user())
    assert await users.delete(record.user_id) is True
    assert await users.get_by_telegram_id(TG_ID) is None
    assert await users.delete(record.user_id) is False

