from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI

from app.api.miniapp import router as miniapp_router
from app.container import build_container
from app.integrations.telegram import TelegramInitDataError, TelegramMiniAppAuth, TelegramWebAppUser
from app.settings import AppSettings


BOT_TOKEN = "123456:TEST_TOKEN"


def make_init_data(*, telegram_user_id: int = 777001, auth_date: int | None = None) -> str:
    auth_date = int(time.time()) if auth_date is None else auth_date
    values = {
        "auth_date": str(auth_date),
        "query_id": "AAE-test-query",
        "user": json.dumps(
            {
                "id": telegram_user_id,
                "first_name": "Dmitry",
                "last_name": "Test",
                "username": "dmitry_test",
                "language_code": "ru",
            },
            separators=(",", ":"),
            ensure_ascii=False,
        ),
    }
    data_check_string = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


def make_test_app():
    settings = AppSettings(telegram_bot_token=BOT_TOKEN)
    container = build_container(settings)
    app = FastAPI()
    app.state.settings = settings
    app.state.container = container
    app.include_router(miniapp_router)
    return app, container


async def grant_consent(container, *telegram_user_ids: int) -> None:
    for telegram_user_id in telegram_user_ids:
        await container.account.accept(TelegramWebAppUser(id=telegram_user_id, first_name="Dmitry"))


def test_telegram_miniapp_auth_validates_signature_and_age() -> None:
    raw = make_init_data()
    parsed = TelegramMiniAppAuth(BOT_TOKEN, max_age_seconds=3600).validate(raw)
    assert parsed.user.id == 777001
    assert parsed.user.first_name == "Dmitry"

    tampered = raw.replace("Dmitry", "Other")
    with pytest.raises(TelegramInitDataError, match="signature"):
        TelegramMiniAppAuth(BOT_TOKEN).validate(tampered)

    expired = make_init_data(auth_date=int(time.time()) - 7200)
    with pytest.raises(TelegramInitDataError, match="expired"):
        TelegramMiniAppAuth(BOT_TOKEN, max_age_seconds=3600).validate(expired)


@pytest.mark.asyncio
async def test_miniapp_auth_crud_default_relationship_and_assist() -> None:
    app, container = make_test_app()
    await grant_consent(container, 777001)
    init_data = make_init_data()
    headers = {"X-Telegram-Init-Data": init_data}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        auth = await client.post("/v1/miniapp/auth", headers=headers)
        assert auth.status_code == 200
        user_id = auth.json()["user"]["user_id"]
        assert user_id.startswith("usr_")

        created = await client.post(
            "/v1/miniapp/relationships",
            headers=headers,
            json={
                "relation_type": "spouse",
                "aliases": ["Аня"],
                "communication_style": {"firmness": "direct"},
            },
        )
        assert created.status_code == 201
        relationship_id = created.json()["relationship_id"]

        bootstrap = await client.get("/v1/miniapp/bootstrap", headers=headers)
        assert bootstrap.status_code == 200
        assert bootstrap.json()["user"]["default_relationship_id"] == relationship_id

        rule = await client.post(
            f"/v1/miniapp/relationships/{relationship_id}/rules",
            headers=headers,
            json={
                "type": "avoid",
                "value": "не использовать фразу 'ты всегда'",
                "priority": 100,
            },
        )
        assert rule.status_code == 201
        assert rule.json()["id"] is not None

        # No relationship_id is supplied: workflow must resolve the user's default.
        assist = await client.post(
            "/v1/miniapp/assist/soften",
            headers=headers,
            json={"text": "Ты всегда всё откладываешь"},
        )
        assert assist.status_code == 200
        request_id = UUID(assist.json()["request_id"])

        checkpoint = await container.checkpoints.load(request_id)
        assert checkpoint is not None
        context = checkpoint.state["artifacts"]["relationship_context"]
        assert context["relationship_id"] == relationship_id
        plan = checkpoint.state["artifacts"]["generation_plan"]
        assert any("ты всегда" in item for item in plan["constraints"])


@pytest.mark.asyncio
async def test_miniapp_cannot_access_another_users_relationship() -> None:
    app, container = make_test_app()
    await grant_consent(container, 7001, 7002)
    user_a_headers = {"X-Telegram-Init-Data": make_init_data(telegram_user_id=7001)}
    user_b_headers = {"X-Telegram-Init-Data": make_init_data(telegram_user_id=7002)}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/v1/miniapp/relationships",
            headers=user_a_headers,
            json={"relation_type": "friend", "aliases": ["Alex"]},
        )
        relationship_id = created.json()["relationship_id"]

        response = await client.put(
            f"/v1/miniapp/me/default-relationship/{relationship_id}",
            headers=user_b_headers,
        )
        assert response.status_code == 404

        assist = await client.post(
            "/v1/miniapp/assist/decode",
            headers=user_b_headers,
            json={"text": "Привет", "relationship_id": relationship_id},
        )
        assert assist.status_code == 404


@pytest.mark.asyncio
async def test_miniapp_rejects_missing_or_invalid_init_data() -> None:
    app, _ = make_test_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        missing = await client.post("/v1/miniapp/auth")
        assert missing.status_code == 401

        invalid = await client.post(
            "/v1/miniapp/auth",
            headers={"X-Telegram-Init-Data": "auth_date=1&hash=bad&user=%7B%7D"},
        )
        assert invalid.status_code == 401


GATED_REQUESTS = [
    ("post", "/v1/miniapp/auth", None),
    ("get", "/v1/miniapp/bootstrap", None),
    ("get", "/v1/miniapp/relationships", None),
    ("post", "/v1/miniapp/relationships", {"relation_type": "friend"}),
    ("put", "/v1/miniapp/me/default-relationship/r-1", None),
    ("post", "/v1/miniapp/relationships/r-1/rules", {"type": "avoid", "value": "x"}),
    ("post", "/v1/miniapp/assist/soften", {"text": "hi"}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path", "body"), GATED_REQUESTS)
async def test_miniapp_requires_consent_and_creates_no_user(method, path, body) -> None:
    app, container = make_test_app()
    headers = {"X-Telegram-Init-Data": make_init_data(telegram_user_id=880001)}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.request(method, path, headers=headers, json=body)
    assert response.status_code == 403
    assert response.json() == {
        "error": "consent_required",
        "message": "Personal data processing consent is required. Open the Telegram bot and send /start.",
        "bot_command": "/start",
    }
    assert await container.users.get_by_telegram_id(880001) is None


@pytest.mark.asyncio
async def test_miniapp_revoked_consent_is_blocked() -> None:
    app, container = make_test_app()
    await grant_consent(container, 880002)
    headers = {"X-Telegram-Init-Data": make_init_data(telegram_user_id=880002)}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.post("/v1/miniapp/auth", headers=headers)).status_code == 200
        await container.account.revoke(880002)
        assert (await client.get("/v1/miniapp/bootstrap", headers=headers)).status_code == 403


def test_miniapp_openapi_declares_consent_required_403() -> None:
    app, _ = make_test_app()
    schema = app.openapi()
    for path, item in schema["paths"].items():
        if not path.startswith("/v1/miniapp/"):
            continue
        for operation in item.values():
            ref = operation["responses"]["403"]["content"]["application/json"]["schema"]["$ref"]
            assert ref.endswith("/ConsentRequiredError")
    declared = schema["components"]["schemas"]["ConsentRequiredError"]
    assert set(declared["properties"]) == {"error", "message", "bot_command"}
    assert declared["additionalProperties"] is False
    assert "ConsentRequiredDetail" not in schema["components"]["schemas"]


@pytest.mark.asyncio
async def test_miniapp_403_runtime_body_validates_against_declared_schema() -> None:
    from app.api.miniapp import ConsentRequiredError

    app, _ = make_test_app()
    headers = {"X-Telegram-Init-Data": make_init_data(telegram_user_id=880003)}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/miniapp/bootstrap", headers=headers)
    assert response.status_code == 403
    body = response.json()
    assert ConsentRequiredError.model_validate(body).model_dump() == body
    assert "detail" not in body
