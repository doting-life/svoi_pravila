from __future__ import annotations

from uuid import uuid4

import pytest

from app.artifacts import AssistRequest, SoftenResult, WorkflowName
from app.tools.llm import LLMGenerateTool
from app.tools.llm.base import StructuredGenerationRequest, StructuredGenerationResponse, StructuredLLMProvider
from tests.envelope import wrap
from tests.test_self_check import make_engine


class WrongIdProvider(StructuredLLMProvider):
    def __init__(self, request_id: object) -> None:
        self.request_id = request_id
        self.calls = 0

    async def generate(self, request: StructuredGenerationRequest) -> StructuredGenerationResponse:
        self.calls += 1
        payload = {
            "request_id": self.request_id,
            "original_intent": "intent",
            "rewritten_message": "Давай обсудим спокойно.",
            "tone_applied": "calm",
            "constraints_respected": [],
        }
        return StructuredGenerationResponse(
            payload=wrap(request, payload), provider="test", model="m", input_tokens=1, output_tokens=1
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "llm_request_id",
    [
        "fd9d7c6d-1acd-4f5e-a202-a202a7325196",  # valid UUID, wrong value (observed in production)
        "not-a-uuid",
        None,
        12345,
    ],
    ids=["valid-but-wrong", "malformed", "null", "wrong-type"],
)
async def test_workflow_uses_authoritative_request_id(llm_request_id) -> None:
    provider = WrongIdProvider(llm_request_id)
    engine, _ = make_engine(provider)
    request = AssistRequest(user_id="u-1", text="Ты опять опоздал.", language="ru")

    result = await engine.execute(WorkflowName.SOFTEN, request)

    assert result.status == "ok"
    assert str(result.structured_result["request_id"]) == str(result.request_id)
    assert str(result.structured_result["request_id"]) != str(llm_request_id)
    assert provider.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("self_check", [True, False])
async def test_tool_overrides_request_id(self_check: bool) -> None:
    authoritative = uuid4()
    tool = LLMGenerateTool(WrongIdProvider("not-a-uuid"))
    result = await tool.execute(
        system_instructions="s",
        user_payload={},
        output_artifact="soften_result",
        self_check=self_check,
        request_id=authoritative,
    )
    assert result.success
    assert isinstance(result.data, SoftenResult)
    assert result.data.request_id == authoritative


@pytest.mark.asyncio
async def test_tool_without_request_id_keeps_existing_validation() -> None:
    tool = LLMGenerateTool(WrongIdProvider("not-a-uuid"))
    result = await tool.execute(
        system_instructions="s", user_payload={}, output_artifact="soften_result", self_check=True
    )
    assert not result.success
