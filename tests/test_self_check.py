from __future__ import annotations

from typing import Any

import pytest

from app.artifacts import AssistRequest, HelpSayResult, WorkflowName
from app.artifacts.internal import SelfCheck
from app.checkpoints import InMemoryCheckpointStore
from app.observability import RequestTraceSink
from app.repositories import InMemoryRelationshipRepository
from app.skills import SkillLoader
from app.stages import StageRegistry
from app.stages.self_check import retry_instructions
from app.tools.llm import FakeStructuredLLMProvider, LLMGenerateTool
from app.tools.llm.base import StructuredGenerationRequest, StructuredGenerationResponse, StructuredLLMProvider
from app.tools.registry import ToolRegistry
from app.workflows.engine import WorkflowDependencies, WorkflowEngine
from tests.envelope import PASSING_SELF_CHECK, wants_envelope

EN_SOURCE = "I want to tell my partner it annoys me when my messages stay unanswered."
EN_BAD = "It upsets me when you leave my messages unanswered. Let's promise to reply faster."
EN_GOOD = "It upsets me when you leave my messages unanswered."
COMMITMENT_FAILURE = {
    "all_passed": False,
    "failures": [{"code": "invented_commitments", "rule_id": None}],
    "violated_rule_ids": [],
}


class ScriptedProvider(StructuredLLMProvider):
    """Help-say provider returning scripted (message, self_check) envelopes."""

    def __init__(self, script: list[tuple[str, Any]]) -> None:
        self.script = script
        self.requests: list[StructuredGenerationRequest] = []

    async def generate(self, request: StructuredGenerationRequest) -> StructuredGenerationResponse:
        self.requests.append(request)
        message, check = self.script[min(len(self.requests), len(self.script)) - 1]
        result = {
            "version": "1.0",
            "request_id": request.user_payload["message_request"]["request_id"],
            "message": message,
            "tone": "calm",
            "preserved_intent": "intent",
            "warnings": [],
        }
        payload: dict[str, Any] = {"result": result}
        if check != "omit":
            payload["self_check"] = check
        return StructuredGenerationResponse(payload=payload, provider="scripted", model="test")


class CountingFakeProvider(FakeStructuredLLMProvider):
    def __init__(self) -> None:
        super().__init__()
        self.requests: list[StructuredGenerationRequest] = []

    async def generate(self, request: StructuredGenerationRequest) -> StructuredGenerationResponse:
        self.requests.append(request)
        return await super().generate(request)


def make_engine(provider: StructuredLLMProvider, mode: str = "required") -> tuple[WorkflowEngine, RequestTraceSink]:
    tools = ToolRegistry()
    tools.register(LLMGenerateTool(provider, self_check_mode=mode))  # type: ignore[arg-type]
    trace = RequestTraceSink()
    engine = WorkflowEngine(
        WorkflowDependencies(
            skills=SkillLoader(),
            tools=tools,
            relationships=InMemoryRelationshipRepository.demo(),
            checkpoints=InMemoryCheckpointStore(),
            trace=trace,
            stages=StageRegistry(),
        )
    )
    return engine, trace


def en_request() -> AssistRequest:
    return AssistRequest(user_id="u-1", text=EN_SOURCE, language="en")


@pytest.mark.asyncio
@pytest.mark.parametrize("workflow", [WorkflowName.SOFTEN, WorkflowName.DECODE, WorkflowName.HELP_SAY])
async def test_normal_path_uses_exactly_one_envelope_call(workflow: WorkflowName) -> None:
    provider = CountingFakeProvider()
    engine, _ = make_engine(provider)
    result = await engine.execute(workflow, AssistRequest(user_id="u-1", text="Ты опять опоздал.", language="ru"))
    assert result.status == "ok"
    assert len(provider.requests) == 1
    assert wants_envelope(provider.requests[0])


@pytest.mark.asyncio
async def test_policies_are_system_only_and_rules_get_ids() -> None:
    provider = CountingFakeProvider()
    engine, _ = make_engine(provider)
    await engine.execute(WorkflowName.SOFTEN, AssistRequest(user_id="u-1", text="Ты опять опоздал.", language="ru"))
    sent = provider.requests[0]
    assert "Workflow policies" in sent.system_instructions
    assert "policies" not in sent.user_payload
    rules = sent.user_payload["relationship_context"]["rules"]
    assert [rule["rule_id"] for rule in rules] == [f"r{i}" for i in range(1, len(rules) + 1)]


@pytest.mark.asyncio
@pytest.mark.parametrize(("check", "status"), [("omit", "missing"), ({"all_passed": "maybe"}, "malformed")])
async def test_required_tool_returns_valid_result_with_status(check: Any, status: str) -> None:
    tool = LLMGenerateTool(ScriptedProvider([(EN_GOOD, check)]), self_check_mode="required")
    result = await tool.execute(
        system_instructions="s",
        user_payload={"message_request": {"request_id": "00000000-0000-0000-0000-000000000001"}},
        output_artifact="help_say_result",
        self_check=True,
    )
    assert result.success
    assert isinstance(result.data, HelpSayResult)
    assert result.metadata["self_check_status"] == status


@pytest.mark.asyncio
@pytest.mark.parametrize(("check", "status"), [("omit", "missing"), ({"all_passed": "maybe"}, "malformed")])
async def test_observe_mode_accepts_result_and_records_status(check: Any, status: str) -> None:
    tool = LLMGenerateTool(ScriptedProvider([(EN_GOOD, check)]), self_check_mode="observe")
    result = await tool.execute(
        system_instructions="s",
        user_payload={"message_request": {"request_id": "00000000-0000-0000-0000-000000000001"}},
        output_artifact="help_say_result",
        self_check=True,
    )
    assert result.success
    assert isinstance(result.data, HelpSayResult)
    assert result.metadata["self_check_status"] == status
    assert "provider_latency_ms" in result.metadata


@pytest.mark.asyncio
async def test_english_self_check_failure_retries_then_passes() -> None:
    provider = ScriptedProvider([(EN_BAD, COMMITMENT_FAILURE), (EN_GOOD, PASSING_SELF_CHECK)])
    engine, _ = make_engine(provider)
    result = await engine.execute(WorkflowName.HELP_SAY, en_request())
    assert result.status == "ok"
    assert result.text == EN_GOOD
    assert len(provider.requests) == 2
    assert "retry_instructions" not in provider.requests[0].user_payload
    assert any("invented_commitments" in i for i in provider.requests[1].user_payload["retry_instructions"])


@pytest.mark.asyncio
async def test_russian_guard_does_not_apply_to_english() -> None:
    provider = ScriptedProvider([(EN_BAD, PASSING_SELF_CHECK)])
    engine, _ = make_engine(provider)
    result = await engine.execute(WorkflowName.HELP_SAY, en_request())
    assert result.status == "ok"
    assert len(provider.requests) == 1


@pytest.mark.asyncio
async def test_trace_is_privacy_safe() -> None:
    provider = ScriptedProvider([(EN_BAD, COMMITMENT_FAILURE), (EN_GOOD, PASSING_SELF_CHECK)])
    engine, trace = make_engine(provider)
    await engine.execute(WorkflowName.HELP_SAY, en_request())
    dumped = repr([event.metadata for event in trace.events])
    assert EN_SOURCE not in dumped
    assert EN_GOOD not in dumped
    assert "invented_commitments" in dumped


def test_retry_instructions_use_codes_and_known_rule_ids_only() -> None:
    check = SelfCheck.model_validate(
        {
            "all_passed": False,
            "failures": [{"code": "relationship_rules_respected", "rule_id": "r2"}],
            "violated_rule_ids": ["r2", "r99"],
        }
    )
    instructions = retry_instructions(check, ["r1", "r2"])
    assert instructions == ["Self-check failure relationship_rules_respected: comply with relationship rule r2."]
