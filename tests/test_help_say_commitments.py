from __future__ import annotations

import pytest

from app.artifacts import AssistRequest, WorkflowName
from app.checkpoints import InMemoryCheckpointStore
from app.observability import RequestTraceSink
from app.repositories import InMemoryRelationshipRepository
from app.skills import SkillLoader
from app.stages import StageRegistry
from app.stages.commitments import find_invented_commitments
from app.tools.llm import FakeStructuredLLMProvider, LLMGenerateTool
from app.tools.llm.base import StructuredGenerationRequest, StructuredGenerationResponse, StructuredLLMProvider
from app.tools.registry import ToolRegistry
from app.workflows.engine import WorkflowDependencies, WorkflowEngine
from tests.envelope import wrap

SOURCE = (
    "Хочу сказать жене, что меня раздражает, когда мои сообщения остаются без ответа, "
    "но не хочу устраивать скандал."
)
BAD = (
    "Меня расстраивает, когда ты долго не отвечаешь на мои сообщения. "
    "Давай стараться быстрее реагировать друг на друга."
)
GOOD = "Меня расстраивает, когда ты долго не отвечаешь на мои сообщения."


class HelpSayProvider(StructuredLLMProvider):
    def __init__(self, messages: list[str]) -> None:
        self.messages = messages
        self.requests: list[StructuredGenerationRequest] = []

    async def generate(self, request: StructuredGenerationRequest) -> StructuredGenerationResponse:
        self.requests.append(request)
        message = self.messages[min(len(self.requests), len(self.messages)) - 1]
        return StructuredGenerationResponse(
            payload=wrap(
                request,
                {
                    "version": "1.0",
                    "request_id": request.user_payload["message_request"]["request_id"],
                    "message": message,
                    "tone": "calm",
                    "preserved_intent": "intent",
                    "warnings": [],
                },
            ),
            provider="help-say-test",
            model="test",
        )


def make_engine(provider: StructuredLLMProvider) -> WorkflowEngine:
    tools = ToolRegistry()
    tools.register(LLMGenerateTool(provider))
    return WorkflowEngine(
        WorkflowDependencies(
            skills=SkillLoader(),
            tools=tools,
            relationships=InMemoryRelationshipRepository.demo(),
            checkpoints=InMemoryCheckpointStore(),
            trace=RequestTraceSink(),
            stages=StageRegistry(),
        )
    )


def request(text: str = SOURCE) -> AssistRequest:
    return AssistRequest(user_id="u-1", text=text, language="ru")


# ---------------------------------------------------------------- detector


def test_real_gigachat_example_is_detected() -> None:
    assert find_invented_commitments(BAD, SOURCE) == ["давай стараться"]


@pytest.mark.parametrize(
    "message, label",
    [
        ("Я обещаю отвечать быстрее.", "я обещаю"),
        ("Я обязуюсь писать первым.", "я обязуюсь"),
        ("Я буду писать реже.", "я буду"),
        ("Я постараюсь не злиться.", "я постараюсь"),
        ("Мы будем чаще созваниваться.", "мы будем"),
        ("Мы постараемся слышать друг друга.", "мы постараемся"),
        ("Давай будем честнее.", "давай будем"),
        ("Давайте постараемся не ссориться.", "давай постараемся"),
        ("Давай договоримся отвечать в течение часа.", "давай договоримся"),
    ],
)
def test_each_pattern_is_recognized(message: str, label: str) -> None:
    assert find_invented_commitments(message, SOURCE) == [label]


def test_explicit_self_promise_in_source_is_allowed() -> None:
    source = "Хочу сказать жене, что я обещаю отвечать на её сообщения быстрее."
    assert find_invented_commitments("Я обещаю отвечать тебе быстрее.", source) == []


def test_explicit_mutual_proposal_in_source_is_allowed() -> None:
    source = "Хочу предложить жене договориться отвечать друг другу быстрее."
    assert find_invented_commitments("Давай договоримся отвечать друг другу быстрее.", source) == []
    assert find_invented_commitments("Давай стараться отвечать друг другу быстрее.", source) == []


@pytest.mark.parametrize(
    "message",
    [
        "Давай поговорим сегодня вечером.",
        "Давай обсудим это спокойно.",
        "Я буду рада, если ты ответишь.",
        "Мне важно, чтобы ты отвечал.",
        "Я злюсь, когда ты не отвечаешь.",
    ],
)
def test_ordinary_phrases_are_not_rejected(message: str) -> None:
    assert find_invented_commitments(message, SOURCE) == []


# ---------------------------------------------------------------- workflow


@pytest.mark.asyncio
async def test_invented_commitment_triggers_retry_and_clean_retry_succeeds() -> None:
    provider = HelpSayProvider([BAD, GOOD])
    result = await make_engine(provider).execute(WorkflowName.HELP_SAY, request())

    assert len(provider.requests) == 2
    assert "retry_instructions" not in provider.requests[0].user_payload
    instructions = provider.requests[1].user_payload["retry_instructions"]
    assert any("'давай стараться'" in item for item in instructions)

    assert result.status == "ok"
    assert result.text == GOOD
    for item in instructions:
        assert item not in (result.text or "")
    assert "retry_instructions" not in (result.structured_result or {})


@pytest.mark.asyncio
async def test_two_violating_attempts_end_with_error_and_no_text() -> None:
    provider = HelpSayProvider([BAD])
    result = await make_engine(provider).execute(WorkflowName.HELP_SAY, request())

    assert len(provider.requests) == 2
    assert result.status == "error"
    assert "стараться" not in str(result.model_dump())


@pytest.mark.asyncio
async def test_ordinary_proposal_passes_first_time() -> None:
    provider = HelpSayProvider(["Давай поговорим сегодня о сообщениях?"])
    result = await make_engine(provider).execute(WorkflowName.HELP_SAY, request())

    assert len(provider.requests) == 1
    assert result.status == "ok"


@pytest.mark.parametrize("workflow", [WorkflowName.SOFTEN, WorkflowName.DECODE])
@pytest.mark.asyncio
async def test_soften_and_decode_unaffected(workflow: WorkflowName) -> None:
    calls: list[StructuredGenerationRequest] = []

    class Recording(FakeStructuredLLMProvider):
        async def generate(self, req: StructuredGenerationRequest) -> StructuredGenerationResponse:
            calls.append(req)
            return await super().generate(req)

    result = await make_engine(Recording()).execute(workflow, request("Ты опять ничего не сделал, давай стараться."))

    assert result.status == "ok"
    assert len(calls) == 1
