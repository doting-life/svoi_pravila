from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.errors import register_provider_error_handlers
from app.api.http import router as assist_router
from app.artifacts import AssistRequest, WorkflowName
from app.stages.delivery import validation_failure_text
from app.tools.llm.base import StructuredGenerationRequest, StructuredGenerationResponse, StructuredLLMProvider
from app.tools.llm.errors import (
    LLMProviderError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUnavailableError,
    safe_retry_after,
)
from tests.test_llm_providers import PRIVATE_TEXT, SECRET_KEY, SECRET_TOKEN, GigaChatServer, _gigachat, _request
from tests.test_self_check import COMMITMENT_FAILURE, EN_BAD, CountingFakeProvider, ScriptedProvider, make_engine

LEAK = "upstream says: quota for account 12345 exceeded"


# --- A. Delivery localization -------------------------------------------------

@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("ru", "Не удалось получить корректный ответ."),
        ("en", "Could not produce a valid response."),
        ("he", "לא הצלחנו להפיק תשובה תקינה."),
        ("ru-RU", "Не удалось получить корректный ответ."),
        ("en_US", "Could not produce a valid response."),
        ("iw", "לא הצלחנו להפיק תשובה תקינה."),
        ("xx", "Could not produce a valid response."),
        ("", "Could not produce a valid response."),
    ],
)
def test_validation_failure_text_mapping(language: str, expected: str) -> None:
    assert validation_failure_text(language) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("language", "expected"),
    [("en", "Could not produce a valid response."), ("he", "לא הצלחנו להפיק תשובה תקינה.")],
)
async def test_validation_exhausted_fallback_is_localized_without_extra_calls(language: str, expected: str) -> None:
    provider = ScriptedProvider([(EN_BAD, COMMITMENT_FAILURE)])
    engine, _ = make_engine(provider)
    result = await engine.execute(
        WorkflowName.HELP_SAY, AssistRequest(user_id="u-1", text="I want to say no.", language=language)
    )
    assert result.status == "error"
    assert result.text == expected
    assert len(provider.requests) == 2  # initial + one validation retry (manifest max_attempts=2)


# --- B. GigaChat provider ----------------------------------------------------

@pytest.mark.asyncio
async def test_gigachat_429_raises_typed_rate_limit_with_retry_after_and_no_retry() -> None:
    server = GigaChatServer(chat_responses=[httpx.Response(429, headers={"Retry-After": "7"}, json={"msg": LEAK})])
    provider = _gigachat(server)
    with pytest.raises(LLMRateLimitError) as excinfo:
        await provider.generate(_request())
    assert excinfo.value.retry_after == "7"
    assert excinfo.value.provider == "gigachat"
    assert len(server.chat_requests) == 1  # no hidden retry
    for secret in (SECRET_KEY, SECRET_TOKEN, PRIVATE_TEXT, LEAK):
        assert secret not in str(excinfo.value)


@pytest.mark.asyncio
async def test_gigachat_429_without_retry_after() -> None:
    provider = _gigachat(GigaChatServer(chat_responses=[httpx.Response(429)]))
    with pytest.raises(LLMRateLimitError) as excinfo:
        await provider.generate(_request())
    assert excinfo.value.retry_after is None


def test_safe_retry_after_accepts_only_delay_seconds() -> None:
    assert safe_retry_after(" 30 ") == "30"
    assert safe_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") is None
    assert safe_retry_after("5\r\nX-Evil: 1") is None
    assert safe_retry_after(None) is None


@pytest.mark.asyncio
async def test_gigachat_timeout_raises_typed_timeout() -> None:
    server = GigaChatServer()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth"):
            return server.handler(request)
        raise httpx.ReadTimeout("timed out", request=request)

    provider = _gigachat(server)
    provider.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(LLMTimeoutError, match="ReadTimeout"):
        await provider.generate(_request())


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [500, 502, 503])
async def test_gigachat_5xx_raises_typed_unavailable(status: int) -> None:
    provider = _gigachat(GigaChatServer(chat_responses=[httpx.Response(status, json={"msg": LEAK})]))
    with pytest.raises(LLMUnavailableError) as excinfo:
        await provider.generate(_request())
    assert LEAK not in str(excinfo.value)


@pytest.mark.asyncio
async def test_gigachat_persistent_401_is_not_rate_limit() -> None:
    server = GigaChatServer(chat_responses=[httpx.Response(401), httpx.Response(401)])
    provider = _gigachat(server)
    with pytest.raises(RuntimeError) as excinfo:
        await provider.generate(_request())
    assert not isinstance(excinfo.value, LLMProviderError)
    assert len(server.auth_requests) == 2
    assert len(server.chat_requests) == 2


# --- C/D. HTTP mapping and observability --------------------------------------

class RaisingProvider(StructuredLLMProvider):
    def __init__(self, exc: Exception) -> None:
        self.exc = exc
        self.calls = 0

    async def generate(self, request: StructuredGenerationRequest) -> StructuredGenerationResponse:
        self.calls += 1
        raise self.exc


def _client(provider: StructuredLLMProvider) -> tuple[TestClient, object]:
    engine, trace = make_engine(provider)
    app = FastAPI()
    register_provider_error_handlers(app)
    app.include_router(assist_router)
    app.state.container = SimpleNamespace(engine=engine)
    return TestClient(app, raise_server_exceptions=False), trace


BODY = {"user_id": "u-1", "text": PRIVATE_TEXT, "language": "en"}


@pytest.mark.parametrize(
    ("exc", "status", "error", "error_type"),
    [
        (LLMRateLimitError(f"GigaChat rate limit {LEAK}", provider="gigachat", retry_after="12"), 429, "provider_rate_limit", "LLMRateLimitError"),
        (LLMTimeoutError("GigaChat request timed out: ReadTimeout", provider="gigachat"), 504, "provider_timeout", "LLMTimeoutError"),
        (LLMUnavailableError("GigaChat unavailable (HTTP 503)", provider="gigachat"), 503, "provider_unavailable", "LLMUnavailableError"),
    ],
)
def test_provider_failures_map_to_http_status(exc: Exception, status: int, error: str, error_type: str) -> None:
    provider = RaisingProvider(exc)
    client, trace = _client(provider)
    response = client.post("/v1/assist/soften", json=BODY)

    assert response.status_code == status
    assert response.json()["error"] == error
    assert "GigaChat" not in response.text and LEAK not in response.text
    assert response.headers.get("Retry-After") == ("12" if status == 429 else None)
    assert provider.calls == 1  # no hidden retry

    [event] = [e for e in trace.events if e.stage == "request"]
    assert event.status == "failed"
    assert event.metadata == {"generate_attempts": 1, "error_type": error_type}
    dumped = repr([e.metadata for e in trace.events])
    assert PRIVATE_TEXT not in dumped and LEAK not in dumped


def test_rate_limit_without_retry_after_has_no_header() -> None:
    client, _ = _client(RaisingProvider(LLMRateLimitError("x", provider="gigachat")))
    response = client.post("/v1/assist/decode", json=BODY)
    assert response.status_code == 429
    assert "Retry-After" not in response.headers


def test_unexpected_exception_still_500() -> None:
    client, _ = _client(RaisingProvider(ValueError(f"bug {LEAK}")))
    response = client.post("/v1/assist/help-say", json=BODY)
    assert response.status_code == 500
    assert LEAK not in response.text


@pytest.mark.parametrize("path", ["soften", "decode", "help-say"])
def test_success_unchanged_with_one_provider_call(path: str) -> None:
    provider = CountingFakeProvider()
    client, _ = _client(provider)
    response = client.post(f"/v1/assist/{path}", json={"user_id": "u-1", "text": "Ты опять опоздал.", "language": "ru"})
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert len(provider.requests) == 1
    json.dumps(response.json())
