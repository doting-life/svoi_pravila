from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.api import telegram as tg
from app.checkpoints import InMemoryCheckpointStore
from app.integrations.telegram import parse_service_command
from app.repositories.consents import InMemoryConsentRepository
from app.repositories.relationships import InMemoryRelationshipRepository
from app.repositories.users import InMemoryUserRepository
from app.services import AccountService

SENDER = {"id": 4242, "first_name": "Ann", "language_code": "ru"}
SETTINGS = SimpleNamespace(telegram_default_workflow="soften")


class FakeTelegram:
    def __init__(self):
        self.calls = []

    async def send_message(self, **kw):
        self.calls.append(("send_message", kw))
        return {}

    async def answer_inline_query(self, **kw):
        self.calls.append(("answer_inline_query", kw))
        return True

    async def answer_callback_query(self, **kw):
        self.calls.append(("answer_callback_query", kw))
        return True

    async def send_document(self, **kw):
        self.calls.append(("send_document", kw))
        return {}


class FakeEngine:
    def __init__(self):
        self.calls = []

    async def execute(self, workflow, request):
        self.calls.append((workflow, request))
        return SimpleNamespace(text="ok-result")


def _container():
    users = InMemoryUserRepository()
    account = AccountService(
        consents=InMemoryConsentRepository(),
        users=users,
        relationships=InMemoryRelationshipRepository(),
        checkpoints=InMemoryCheckpointStore(),
    )
    return SimpleNamespace(telegram=FakeTelegram(), engine=FakeEngine(), users=users, account=account)


def _msg(text):
    return {"message": {"text": text, "from": SENDER, "chat": {"id": 4242}}}


def _cb(data):
    return {"callback_query": {"id": "cb1", "from": SENDER, "data": data, "message": {"chat": {"id": 4242}}}}


def test_parse_service_command():
    assert parse_service_command("/start") == "start"
    assert parse_service_command("/delete@SvoiBot") == "delete"
    assert parse_service_command(" /EXPORT now") == "export"
    assert parse_service_command("/revoke") == "revoke"
    assert parse_service_command("/soften hi") is None
    assert parse_service_command("start") is None
    assert parse_service_command("/started") is None


DEEP_LINK_START_VARIANTS = [
    "/start",
    "/start consent",
    "/start@DotingLifeBot",
    "/start@DotingLifeBot consent",
]


@pytest.mark.parametrize("text", DEEP_LINK_START_VARIANTS)
def test_parse_service_command_accepts_deep_link_start(text):
    assert parse_service_command(text) == "start"


@pytest.mark.parametrize("text", DEEP_LINK_START_VARIANTS)
async def test_deep_link_start_shows_consent_not_workflow_or_gate(text):
    c = _container()
    await tg._handle_update(c, SETTINGS, _msg(text))
    assert c.engine.calls == []
    assert await c.users.get_by_telegram_id(4242) is None
    name, kw = c.telegram.calls[-1]
    assert name == "send_message"
    assert kw["text"] == tg.CONSENT_TEXT
    assert kw["text"] != tg.CONSENT_REQUIRED_TEXT


async def test_workflow_message_gated_without_consent_and_no_user_created():
    c = _container()
    await tg._handle_update(c, SETTINGS, _msg("/soften hello"))
    assert c.engine.calls == []
    assert await c.users.get_by_telegram_id(4242) is None
    assert c.telegram.calls[-1][1]["text"] == tg.CONSENT_REQUIRED_TEXT


async def test_inline_query_gated_without_consent():
    c = _container()
    await tg._handle_update(c, SETTINGS, {"inline_query": {"id": "q1", "query": "soften: hi", "from": SENDER}})
    assert c.engine.calls == []
    assert await c.users.get_by_telegram_id(4242) is None
    name, kw = c.telegram.calls[-1]
    assert name == "answer_inline_query"
    assert kw["results"][0]["input_message_content"]["message_text"] == tg.CONSENT_REQUIRED_TEXT


async def test_start_shows_consent_with_buttons_and_no_user():
    c = _container()
    await tg._handle_update(c, SETTINGS, _msg("/start"))
    name, kw = c.telegram.calls[-1]
    assert kw["text"] == tg.CONSENT_TEXT
    data = [b["callback_data"] for b in kw["reply_markup"]["inline_keyboard"][0]]
    assert data == [tg.CB_CONSENT_ACCEPT, tg.CB_CONSENT_DECLINE]
    assert await c.users.get_by_telegram_id(4242) is None


async def test_decline_keeps_gate():
    c = _container()
    await tg._handle_update(c, SETTINGS, _msg("/start"))
    await tg._handle_update(c, SETTINGS, _cb(tg.CB_CONSENT_DECLINE))
    assert c.telegram.calls[-2][0] == "answer_callback_query"
    await tg._handle_update(c, SETTINGS, _msg("/soften hi"))
    assert c.engine.calls == []


async def test_accept_then_workflow_runs():
    c = _container()
    await tg._handle_update(c, SETTINGS, _msg("/start"))
    await tg._handle_update(c, SETTINGS, _cb(tg.CB_CONSENT_ACCEPT))
    assert await c.users.get_by_telegram_id(4242) is not None
    await tg._handle_update(c, SETTINGS, _msg("/soften hi"))
    assert len(c.engine.calls) == 1
    assert c.telegram.calls[-1][1]["text"] == "ok-result"


async def test_revoke_blocks_further_workflows():
    c = _container()
    await tg._handle_update(c, SETTINGS, _cb(tg.CB_CONSENT_ACCEPT))
    await tg._handle_update(c, SETTINGS, _msg("/revoke"))
    assert c.telegram.calls[-1][1]["text"] == tg.REVOKED_TEXT
    await tg._handle_update(c, SETTINGS, _msg("/soften hi"))
    assert c.engine.calls == []


async def test_service_commands_available_without_consent():
    c = _container()
    await tg._handle_update(c, SETTINGS, _msg("/revoke"))
    assert c.telegram.calls[-1][1]["text"] == tg.NOT_ACTIVE_REVOKE_TEXT
    await tg._handle_update(c, SETTINGS, _msg("/export"))
    assert c.telegram.calls[-1][1]["text"] == tg.EXPORT_PROMPT_TEXT
    await tg._handle_update(c, SETTINGS, _msg("/delete"))
    assert c.telegram.calls[-1][1]["text"] == tg.DELETE_PROMPT_TEXT


async def test_delete_flow_and_cancel():
    c = _container()
    await tg._handle_update(c, SETTINGS, _cb(tg.CB_CONSENT_ACCEPT))
    await tg._handle_update(c, SETTINGS, _msg("/delete"))
    await tg._handle_update(c, SETTINGS, _cb(tg.CB_DELETE_CANCEL))
    assert c.telegram.calls[-1][1]["text"] == tg.CANCELLED_TEXT
    await tg._handle_update(c, SETTINGS, _cb(tg.CB_DELETE_CONFIRM))
    assert c.telegram.calls[-1][1]["text"] == tg.EXPIRED_TEXT
    assert await c.users.get_by_telegram_id(4242) is not None
    await tg._handle_update(c, SETTINGS, _msg("/delete"))
    await tg._handle_update(c, SETTINGS, _cb(tg.CB_DELETE_CONFIRM))
    assert c.telegram.calls[-1][1]["text"] == tg.DELETE_DONE_TEXT
    assert await c.users.get_by_telegram_id(4242) is None


async def test_export_sends_document():
    c = _container()
    await tg._handle_update(c, SETTINGS, _cb(tg.CB_CONSENT_ACCEPT))
    await tg._handle_update(c, SETTINGS, _msg("/export"))
    await tg._handle_update(c, SETTINGS, _cb(tg.CB_EXPORT_CONFIRM))
    name, kw = c.telegram.calls[-1]
    assert name == "send_document"
    assert kw["filename"].endswith(".json") and kw["content"]


async def test_start_is_idempotent_for_current_consent():
    c = _container()
    await tg._handle_update(c, SETTINGS, _cb(tg.CB_CONSENT_ACCEPT))
    await tg._handle_update(c, SETTINGS, _msg("/start"))
    assert c.telegram.calls[-1][1]["text"] == tg.ALREADY_ACTIVE_TEXT
    assert "reply_markup" not in c.telegram.calls[-1][1]
    record = await c.account.consents.get_onboarding(4242)
    assert record.state == "active"
    assert await c.account.is_active(4242) is True


async def test_start_after_revoke_sets_pending_and_shows_consent():
    c = _container()
    await tg._handle_update(c, SETTINGS, _cb(tg.CB_CONSENT_ACCEPT))
    await tg._handle_update(c, SETTINGS, _msg("/revoke"))
    await tg._handle_update(c, SETTINGS, _msg("/start"))
    assert c.telegram.calls[-1][1]["text"] == tg.CONSENT_TEXT
    assert (await c.account.consents.get_onboarding(4242)).state == "consent_pending"


async def test_start_with_stale_consent_version_shows_consent():
    c = _container()
    await c.account.consents.accept(4242, version="old", text_sha256="0" * 64)
    assert await c.account.is_active(4242) is False
    await tg._handle_update(c, SETTINGS, _msg("/start"))
    assert c.telegram.calls[-1][1]["text"] == tg.CONSENT_TEXT
    assert (await c.account.consents.get_onboarding(4242)).state == "consent_pending"


async def test_missing_account_service_is_explicit_error():
    import pytest

    c = SimpleNamespace(telegram=FakeTelegram(), engine=FakeEngine(), users=InMemoryUserRepository())
    with pytest.raises(RuntimeError, match="AccountService"):
        await tg._handle_update(c, SETTINGS, _msg("/soften hi"))


async def test_callback_answered_before_delete_and_export_work():
    for data, command in ((tg.CB_DELETE_CONFIRM, "/delete"), (tg.CB_EXPORT_CONFIRM, "/export")):
        c = _container()
        await tg._handle_update(c, SETTINGS, _cb(tg.CB_CONSENT_ACCEPT))
        await tg._handle_update(c, SETTINGS, _msg(command))
        start = len(c.telegram.calls)
        await tg._handle_update(c, SETTINGS, _cb(data))
        assert c.telegram.calls[start][0] == "answer_callback_query"


def test_container_registers_account_service():
    from app.container import build_container

    container = build_container()
    assert isinstance(container.account, AccountService)
    assert container.account.consents is container.consents
