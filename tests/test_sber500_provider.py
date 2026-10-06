from __future__ import annotations

import json
from uuid import uuid4

import httpx
import pytest

from app.artifacts import AssistRequest, SoftenResult, WorkflowName
from app.artifacts.internal import envelope_for
from app.container import build_llm_provider
from app.settings import AppSettings
from app.tools.llm import LLMGenerateTool, Sber500StructuredLLMProvider
from app.tools.llm.base import StructuredGenerationRequest
from app.tools.llm.errors import LLMRateLimitError, LLMTimeoutError, LLMUnavailableError
from tests.envelope import PASSING_SELF_CHECK

SECRET = "sk-sber500-secret-key"
PRIVATE_TEXT = "Ты опять опоздал. אתה שוב איחרת."
LEAK = "gateway internal trace id 999 / upstream body"
URL = "https://shared1.multitool.works:4000/v1/chat/completions"


def _settings(**overrides) -> AppSettings:
    return AppSettings(_env_file=None, **overrides)


def _soften(message: str = "Мягкое сообщение. הודעה רכה.") -> dict:
    return {
        "request_id": str(uuid4()),
        "original_intent": "намерение",
        "rewritten_message": message,
        "tone_applied": "calm",
        "constraints_respected": [],
    }


def _request(output_model=SoftenResult) -> StructuredGenerationRequest:
    return StructuredGenerationRequest(
        system_instructions="system rules",
        user_payload={"message_request": {"text": PRIVATE_TEXT}},
        output_model=output_model,
        metadata={},
    )


class Gateway:
    def __init__(self, responses: list[httpx.Response] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.responses = responses or []

    def ok(self, payload: dict, usage: dict | None = None) -> httpx.Response:
        body = {
            "model": "gigachat-3-pro",
            "choices": [{"message": {"role": "assistant", "content": json.dumps(payload, ensure_ascii=False)}}],
        }
        if usage is not None:
            body["usage"] = usage
        return httpx.Response(200, json=body)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.responses:
            return self.responses.pop(0)
        return self.ok(_soften(), {"prompt_tokens": 1241, "completion_tokens": 17})


def _provider(gateway: Gateway) -> Sber500StructuredLLMProvider:
    return Sber500StructuredLLMProvider(
        api_key=SECRET,
        model="gigachat-3-pro",
        client=httpx.AsyncClient(transport=httpx.MockTransport(gateway.handler)),
    )


# ----------------------------------------------------------------- settings

@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({}, "SBER500_API_KEY"),
        ({"sber500_api_key": ""}, "SBER500_API_KEY"),
        ({"sber500_api_key": SECRET, "sber500_model": ""}, "SBER500_MODEL"),
    ],
)
def test_settings_require_sber500_key_and_model(overrides, message) -> None:
    with pytest.raises(ValueError, match=message) as excinfo:
        _settings(llm_provider="sber500", **overrides)
    assert SECRET not in str(excinfo.value)


def test_settings_defaults_and_other_providers_unaffected() -> None:
    settings = _settings(llm_provider="sber500", sber500_api_key=SECRET)
    assert settings.sber500_model == "gigachat-3-pro"
    assert settings.sber500_base_url == "https://shared1.multitool.works:4000/v1"
    assert settings.sber500_timeout_seconds == 30.0
    assert SECRET not in repr(settings)
    assert _settings().llm_provider == "fake"
    _settings(llm_provider="gigachat", gigachat_credentials="c", gigachat_model="GigaChat")


@pytest.mark.asyncio
async def test_build_llm_provider_selects_sber500() -> None:
    provider, closer = build_llm_provider(
        _settings(llm_provider="sber500", sber500_api_key=SECRET, sber500_model="gigachat-3-pro")
    )
    assert isinstance(provider, Sber500StructuredLLMProvider)
    assert SECRET not in repr(provider)
    assert closer is not None
    await closer()


# ----------------------------------------------------------------- success

@pytest.mark.asyncio
async def test_sber500_request_and_response() -> None:
    gateway = Gateway()
    response = await _provider(gateway).generate(_request())

    [sent] = gateway.requests
    assert str(sent.url) == URL
    assert sent.method == "POST"
    assert sent.headers["Authorization"] == f"Bearer {SECRET}"
    assert sent.headers["Content-Type"].startswith("application/json")
    body = json.loads(sent.content.decode("utf-8"))
    assert body["model"] == "gigachat-3-pro"
    assert body["stream"] is False
    assert body["messages"][0] == {"role": "system", "content": "system rules"}
    assert body["messages"][1]["role"] == "user"
    assert json.loads(body["messages"][1]["content"]) == _request().user_payload
    fmt = body["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["name"] == "SoftenResult"
    assert fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"] == SoftenResult.model_json_schema()

    SoftenResult.model_validate(response.payload)
    assert response.provider == "sber500"
    assert response.model == "gigachat-3-pro"
    assert (response.input_tokens, response.output_tokens) == (1241, 17)
    assert response.cost_usd is None


@pytest.mark.asyncio
async def test_sber500_missing_usage_and_model_fallback() -> None:
    gateway = Gateway([httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(_soften())}}]})])
    response = await _provider(gateway).generate(_request())
    assert (response.input_tokens, response.output_tokens, response.model, response.cost_usd) == (0, 0, "gigachat-3-pro", None)


@pytest.mark.asyncio
async def test_sber500_utf8_roundtrip() -> None:
    message = "Давай обсудим спокойно. בוא נדבר ברוגע."
    gateway = Gateway()
    gateway.responses = [gateway.ok(_soften(message))]
    response = await _provider(gateway).generate(_request())

    raw = gateway.requests[0].content
    assert PRIVATE_TEXT.encode("utf-8") in raw  # ensure_ascii=False, real UTF-8 on the wire
    assert response.payload["rewritten_message"] == message


@pytest.mark.asyncio
async def test_sber500_envelope_self_check_one_call() -> None:
    envelope = {"result": _soften(), "self_check": PASSING_SELF_CHECK}
    gateway = Gateway()
    gateway.responses = [gateway.ok(envelope, {"prompt_tokens": 10, "completion_tokens": 5})]
    tool = LLMGenerateTool(_provider(gateway), self_check_mode="required")

    result = await tool.execute(
        system_instructions="s",
        user_payload={"x": 1},
        output_artifact="soften_result",
        self_check=True,
    )

    assert result.success
    assert isinstance(result.data, SoftenResult)
    assert result.metadata["self_check_status"] == "present"
    assert result.metadata["provider"] == "sber500"
    assert len(gateway.requests) == 1
    sent = json.loads(gateway.requests[0].content)
    assert sent["response_format"]["json_schema"]["schema"] == envelope_for(SoftenResult).model_json_schema()


@pytest.mark.asyncio
async def test_sber500_engine_normal_path_is_one_call() -> None:
    from tests.test_self_check import make_engine

    class Wrapped(Sber500StructuredLLMProvider):
        pass

    gateway = Gateway()

    def handler(request: httpx.Request) -> httpx.Response:
        gateway.requests.append(request)
        out = json.loads(json.loads(request.content)["messages"][1]["content"])
        rid = out["message_request"]["request_id"]
        payload = {"result": {**_soften(), "request_id": rid}, "self_check": PASSING_SELF_CHECK}
        return gateway.ok(payload, {"prompt_tokens": 3, "completion_tokens": 4})

    provider = Wrapped(api_key=SECRET, model="gigachat-3-pro", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    engine, trace = make_engine(provider)
    result = await engine.execute(WorkflowName.SOFTEN, AssistRequest(user_id="u-1", text=PRIVATE_TEXT, language="ru"))
    assert result.status == "ok"
    assert len(gateway.requests) == 1
    generate = next(e for e in trace.events if e.stage == "generate")
    assert generate.metadata["provider"] == "sber500"
    assert PRIVATE_TEXT not in repr([e.metadata for e in trace.events])


# ----------------------------------------------------------------- errors

def _assert_no_leak(exc: BaseException) -> None:
    text = str(exc)
    for secret in (SECRET, PRIVATE_TEXT, LEAK):
        assert secret not in text


@pytest.mark.asyncio
async def test_sber500_429_typed_with_retry_after_and_no_retry() -> None:
    gateway = Gateway([httpx.Response(429, headers={"Retry-After": "15"}, json={"error": LEAK})])
    with pytest.raises(LLMRateLimitError) as excinfo:
        await _provider(gateway).generate(_request())
    assert excinfo.value.retry_after == "15"
    assert excinfo.value.provider == "sber500"
    assert len(gateway.requests) == 1
    _assert_no_leak(excinfo.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_sber500_5xx_typed_unavailable(status: int) -> None:
    gateway = Gateway([httpx.Response(status, json={"error": LEAK})])
    with pytest.raises(LLMUnavailableError) as excinfo:
        await _provider(gateway).generate(_request())
    assert len(gateway.requests) == 1
    _assert_no_leak(excinfo.value)


@pytest.mark.asyncio
async def test_sber500_timeout_typed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    provider = Sber500StructuredLLMProvider(
        api_key=SECRET, model="gigachat-3-pro", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(LLMTimeoutError) as excinfo:
        await provider.generate(_request())
    _assert_no_leak(excinfo.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401, json={"error": LEAK}),
        httpx.Response(400, json={"error": LEAK}),
        httpx.Response(200, json={"choices": [{"message": {"content": "{not json " + PRIVATE_TEXT}}]}),
        httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"tone_applied": PRIVATE_TEXT})}}]}),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, text="<html>" + LEAK),
    ],
    ids=["401", "400", "malformed-json", "schema-invalid", "bad-shape", "non-json"],
)
async def test_sber500_contract_failures_are_safe_and_untyped(response: httpx.Response) -> None:
    with pytest.raises(Exception) as excinfo:
        await _provider(Gateway([response])).generate(_request())
    assert not isinstance(excinfo.value, (LLMRateLimitError, LLMTimeoutError, LLMUnavailableError))
    _assert_no_leak(excinfo.value)


@pytest.mark.asyncio
async def test_sber500_network_error_is_safe() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable " + SECRET, request=request)

    provider = Sber500StructuredLLMProvider(
        api_key=SECRET, model="m", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(RuntimeError, match="ConnectError") as excinfo:
        await provider.generate(_request())
    _assert_no_leak(excinfo.value)


def test_sber500_constructor_requires_key_and_model() -> None:
    with pytest.raises(ValueError, match="SBER500_API_KEY"):
        Sber500StructuredLLMProvider(api_key="", model="m")
    with pytest.raises(ValueError, match="SBER500_MODEL"):
        Sber500StructuredLLMProvider(api_key=SECRET, model="")
