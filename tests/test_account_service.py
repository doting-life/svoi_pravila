from __future__ import annotations

import json

import pytest

from app.checkpoints import InMemoryCheckpointStore
from app.integrations.telegram import TelegramWebAppUser
from app.repositories.consents import InMemoryConsentRepository
from app.repositories.relationships import InMemoryRelationshipRepository, RelationshipCreate
from app.repositories.users import InMemoryUserRepository
from app.services import AccountService


TG = TelegramWebAppUser(id=777, first_name="Ann", language_code="ru")


class FailingCheckpoints(InMemoryCheckpointStore):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def delete_for_user(self, user_id: str) -> int:
        self.calls += 1
        raise RuntimeError("redis down")


def _service(checkpoints=None, users=None):
    return AccountService(
        consents=InMemoryConsentRepository(),
        users=users or InMemoryUserRepository(),
        relationships=InMemoryRelationshipRepository(),
        checkpoints=checkpoints or InMemoryCheckpointStore(),
    )


async def test_start_does_not_create_user_and_is_inactive():
    svc = _service()
    record = await svc.start(TG.id)
    assert record.state == "consent_pending"
    assert await svc.is_active(TG.id) is False
    assert await svc.users.get_by_telegram_id(TG.id) is None


async def test_decline_keeps_pending_and_no_user():
    svc = _service()
    await svc.start(TG.id)
    record = await svc.decline(TG.id)
    assert record.state == "consent_pending"
    assert await svc.users.get_by_telegram_id(TG.id) is None


async def test_accept_creates_user_and_activates():
    svc = _service()
    user = await svc.accept(TG)
    assert user.telegram_user_id == TG.id
    assert await svc.is_active(TG.id) is True


async def test_revoke_deactivates_but_keeps_data():
    svc = _service()
    await svc.accept(TG)
    assert await svc.revoke(TG.id) is True
    assert await svc.is_active(TG.id) is False
    assert await svc.users.get_by_telegram_id(TG.id) is not None


async def test_confirm_delete_requires_request():
    svc = _service()
    await svc.accept(TG)
    result = await svc.confirm_delete(TG.id)
    assert result.status == "not_requested"
    assert await svc.is_active(TG.id) is True


async def test_delete_removes_data_and_cleans_redis_after_db():
    svc = _service()
    user = await svc.accept(TG)
    await svc.relationships.create(user.user_id, RelationshipCreate(relation_type="partner"))
    calls = []
    original = svc.checkpoints.delete_for_user

    async def tracking(user_id):
        calls.append((user_id, await svc.users.get(user_id)))
        return await original(user_id)

    svc.checkpoints.delete_for_user = tracking
    await svc.request_delete(TG.id)
    result = await svc.confirm_delete(TG.id)
    assert result.status == "deleted"
    assert calls == [(user.user_id, None)]
    assert await svc.users.get_by_telegram_id(TG.id) is None
    assert await svc.relationships.list_for_user(user.user_id) == []
    assert await svc.consents.list_consents(TG.id) == []
    assert await svc.is_active(TG.id) is False


async def test_delete_redis_failure_still_reports_deleted():
    checkpoints = FailingCheckpoints()
    svc = _service(checkpoints=checkpoints)
    await svc.accept(TG)
    await svc.request_delete(TG.id)
    result = await svc.confirm_delete(TG.id)
    assert result.status == "deleted"
    assert result.redis_cleanup_failed is True
    assert checkpoints.calls == 1


async def test_delete_db_failure_keeps_revoked_and_skips_redis():
    class BrokenUsers(InMemoryUserRepository):
        async def delete(self, user_id: str) -> bool:
            raise RuntimeError("db down")

    checkpoints = FailingCheckpoints()
    svc = _service(checkpoints=checkpoints, users=BrokenUsers())
    await svc.accept(TG)
    await svc.request_delete(TG.id)
    result = await svc.confirm_delete(TG.id)
    assert result.status == "failed"
    assert checkpoints.calls == 0
    assert await svc.is_active(TG.id) is False


async def test_export_requires_request_and_returns_json():
    svc = _service()
    user = await svc.accept(TG)
    assert (await svc.confirm_export(TG.id)).status == "not_requested"
    await svc.request_export(TG.id)
    result = await svc.confirm_export(TG.id)
    assert result.status == "ok"
    payload = json.loads(result.content.decode("utf-8"))
    assert payload["user"]["user_id"] == user.user_id
    assert payload["consents"][0]["version"]
    assert (await svc.confirm_export(TG.id)).status == "not_requested"
