"""Smoke test: examples/python/miniapp_client.py runs end-to-end against the
in-process Mini App API with the default fake/memory container."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from app.api.miniapp import BootstrapResponse, RelationshipView
from app.api.miniapp import router as miniapp_router
from app.artifacts import DeliveryResponse, RelationshipRule
from app.container import build_container
from app.integrations.telegram import TelegramWebAppUser
from app.settings import AppSettings


ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_PATH = ROOT / "examples" / "python" / "miniapp_client.py"
BOT_TOKEN = "123456:EXAMPLE_TOKEN"


def _load_example():
    spec = importlib.util.spec_from_file_location("miniapp_client_example", EXAMPLE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


async def _make_app() -> FastAPI:
    settings = AppSettings(telegram_bot_token=BOT_TOKEN)
    app = FastAPI()
    app.state.settings = settings
    app.state.container = build_container(settings)
    # The example signs initData for Telegram user 777001; consent is given in the bot before Mini App use.
    await app.state.container.account.accept(TelegramWebAppUser(id=777001, first_name="Example"))
    app.include_router(miniapp_router)
    return app


@pytest.mark.asyncio
async def test_python_example_scenario_runs_against_api() -> None:
    example = _load_example()
    transport = httpx.ASGITransport(app=await _make_app())
    init_data = example.sign_init_data(BOT_TOKEN)

    async with example.MiniAppClient("http://test", init_data, transport=transport) as client:
        result = await example.run_scenario(client)

    relationship = RelationshipView.model_validate(result["relationship"])
    RelationshipRule.model_validate(result["rule"])
    bootstrap = BootstrapResponse.model_validate(result["bootstrap"])
    delivery = DeliveryResponse.model_validate(result["delivery"])

    assert bootstrap.user.default_relationship_id == relationship.relationship_id
    assert [r.relationship_id for r in bootstrap.relationships] == [relationship.relationship_id]
    assert delivery.workflow == "soften"
    assert delivery.status in {"ok", "blocked", "error"}


@pytest.mark.asyncio
async def test_python_example_maps_errors() -> None:
    example = _load_example()
    transport = httpx.ASGITransport(app=await _make_app())

    async with example.MiniAppClient("http://test", "invalid", transport=transport) as client:
        with pytest.raises(example.MiniAppApiError) as unauthorized:
            await client.bootstrap()
    assert unauthorized.value.status == 401

    async with example.MiniAppClient(
        "http://test", example.sign_init_data(BOT_TOKEN), transport=transport
    ) as client:
        with pytest.raises(example.MiniAppApiError) as missing:
            await client.set_default_relationship("rel_missing")
    assert missing.value.status == 404
