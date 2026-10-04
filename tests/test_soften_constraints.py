from __future__ import annotations

import pytest

from app.artifacts import AssistRequest, RelationshipContext, RelationshipRule, WorkflowName
from app.checkpoints import InMemoryCheckpointStore
from app.observability import RequestTraceSink
from app.repositories import InMemoryRelationshipRepository
from app.skills import SkillLoader
from app.stages import StageRegistry
from app.stages.constraints import avoided_phrases, find_avoided_phrases
from app.tools.llm import FakeStructuredLLMProvider, LLMGenerateTool
from app.tools.llm.base import StructuredGenerationRequest, StructuredGenerationResponse, StructuredLLMProvider
from app.tools.registry import ToolRegistry
from app.workflows.engine import WorkflowDependencies, WorkflowEngine

AVOID_RULE = "\u043d\u0435 \u0438\u0441\u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u044c \u043a\u0430\u0442\u0435\u0433\u043e\u0440\u0438\u0447\u043d\u044b\u0435 '\u0442\u044b \u0432\u0441\u0435\u0433\u0434\u0430' \u0438 '\u0442\u044b \u043d\u0438\u043a\u043e\u0433\u0434\u0430'"
TONE_RULE = "\u0433\u043e\u0432\u043e\u0440\u0438\u0442\u044c \u043f\u0440\u044f\u043c\u043e \u0438 \u0435\u0441\u0442\u0435\u0441\u0442\u0432\u0435\u043d\u043d\u043e, \u0431\u0435\u0437 \u0442\u0435\u0440\u0430\u043f\u0435\u0432\u0442\u0438\u0447\u0435\u0441\u043a\u0438\u0445 \u0448\u0442\u0430\u043c\u043f\u043e\u0432"
SOURCE = "\u0422\u044b \u0432\u0441\u0435\u0433\u0434\u0430 \u043c\u0435\u043d\u044f \u0438\u0433\u043d\u043e\u0440\u0438\u0440\u0443\u0435\u0448\u044c \u0438 \u043d\u0438\u043a\u043e\u0433\u0434\u0430 \u043d\u0435 \u043e\u0442\u0432\u0435\u0447\u0430\u0435\u0448\u044c \u043d\u0430 \u043c\u043e\u0438 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u044f."
GOOD = "\u041c\u043d\u0435 \u043e\u0431\u0438\u0434\u043d\u043e, \u043a\u043e\u0433\u0434\u0430 \u043c\u043e\u0438 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u044f \u043e\u0441\u0442\u0430\u044e\u0442\u0441\u044f \u0431\u0435\u0437 \u043e\u0442\u0432\u0435\u0442\u0430."
BAD = "\u0422\u042b \u0412\u0421\u0415\u0413\u0414\u0410 \u043c\u0435\u043d\u044f \u0438\u0433\u043d\u043e\u0440\u0438\u0440\u0443\u0435\u0448\u044c."


class RecordingProvider(StructuredLLMProvider):
    """Returns scripted rewrites and records every generation request."""

    def __init__(self, rewrites: list[str]) -> None:
        self.rewrites = rewrites
        self.requests: list[StructuredGenerationRequest] = []

    async def generate(self, request: StructuredGenerationRequest) -> StructuredGenerationResponse:
        self.requests.append(request)
        rewritten = self.rewrites[min(len(self.requests), len(self.rewrites)) - 1]
        constraints: list[str] = request.user_payload["generation_plan"]["constraints"]
        respected = [c for c in constraints if c in (AVOID_RULE, TONE_RULE)]
        return StructuredGenerationResponse(
            payload={
                "version": "1.0",
                "request_id": request.user_payload["message_request"]["request_id"],
                "original_intent": "intent",
                "rewritten_message": rewritten,
                "tone_applied": "direct",
                "constraints_respected": respected,
            },
            provider="recording",
            model="test",
        )


def make_engine(provider: StructuredLLMProvider) -> tuple[WorkflowEngine, InMemoryCheckpointStore]:
    tools = ToolRegistry()
    tools.register(LLMGenerateTool(provider))
    checkpoints = InMemoryCheckpointStore()
    engine = WorkflowEngine(
        WorkflowDependencies(
            skills=SkillLoader(),
            tools=tools,
            relationships=InMemoryRelationshipRepository.demo(),
            checkpoints=checkpoints,
            trace=RequestTraceSink(),
            stages=StageRegistry(),
        )
    )
    return engine, checkpoints


def soften_request() -> AssistRequest:
    return AssistRequest(user_id="u-1", relationship_id="partner-1", text=SOURCE, language="ru")


# ------------------------------------------------------------------ payload


@pytest.mark.asyncio
async def test_generation_payload_contains_plan_constraints() -> None:
    provider = RecordingProvider([GOOD])
    engine, _ = make_engine(provider)
    await engine.execute(WorkflowName.SOFTEN, soften_request())

    payload = provider.requests[0].user_payload
    assert AVOID_RULE in payload["generation_plan"]["constraints"]
    assert TONE_RULE in payload["generation_plan"]["constraints"]
    assert [rule["value"] for rule in payload["relationship_context"]["rules"]] == [AVOID_RULE, TONE_RULE]


@pytest.mark.asyncio
async def test_soften_skill_instructs_constraints_respected_population() -> None:
    provider = RecordingProvider([GOOD])
    engine, _ = make_engine(provider)
    await engine.execute(WorkflowName.SOFTEN, soften_request())

    instructions = provider.requests[0].system_instructions
    assert "## constraints_respected" in instructions
    assert "verbatim" in instructions
    assert "Never mention constraints" in instructions


# ------------------------------------------------------------------ results


@pytest.mark.asyncio
async def test_structured_soften_result_keeps_non_empty_constraints_respected() -> None:
    provider = RecordingProvider([GOOD])
    engine, _ = make_engine(provider)
    result = await engine.execute(WorkflowName.SOFTEN, soften_request())

    assert result.status == "ok"
    assert result.text == GOOD
    assert result.structured_result is not None
    assert result.structured_result["constraints_respected"] == [AVOID_RULE, TONE_RULE]
    assert AVOID_RULE not in result.text


@pytest.mark.asyncio
async def test_fake_provider_reports_applicable_plan_constraints_only() -> None:
    engine, _ = make_engine(FakeStructuredLLMProvider())
    result = await engine.execute(WorkflowName.SOFTEN, soften_request())

    assert result.status == "ok"
    assert result.structured_result is not None
    assert result.structured_result["constraints_respected"] == [AVOID_RULE]


@pytest.mark.asyncio
async def test_fake_provider_lists_nothing_when_avoid_rule_irrelevant() -> None:
    engine, _ = make_engine(FakeStructuredLLMProvider())
    request = AssistRequest(user_id="u-1", relationship_id="partner-1", text="\u041f\u0440\u0438\u0432\u0435\u0442", language="ru")
    result = await engine.execute(WorkflowName.SOFTEN, request)

    assert result.structured_result is not None
    assert result.structured_result["constraints_respected"] == []


# --------------------------------------------------------------- validation


@pytest.mark.asyncio
async def test_violated_avoid_rule_triggers_retry_then_passes() -> None:
    provider = RecordingProvider([BAD, GOOD])
    engine, checkpoints = make_engine(provider)
    result = await engine.execute(WorkflowName.SOFTEN, soften_request())

    assert len(provider.requests) == 2
    assert result.status == "ok"
    assert result.text == GOOD
    checkpoint = await checkpoints.load(result.request_id)
    assert checkpoint is not None
    validation = checkpoint.state["artifacts"]["validation_result"]
    check = next(c for c in validation["semantic_checks"] if c["name"] == "avoided_phrases_absent")
    assert check["passed"] is True


@pytest.mark.asyncio
async def test_persistent_avoid_rule_violation_is_not_delivered() -> None:
    provider = RecordingProvider([BAD])
    engine, checkpoints = make_engine(provider)
    result = await engine.execute(WorkflowName.SOFTEN, soften_request())

    assert len(provider.requests) == 2
    assert result.status == "error"
    assert BAD not in result.text
    checkpoint = await checkpoints.load(result.request_id)
    assert checkpoint is not None
    validation = checkpoint.state["artifacts"]["validation_result"]
    assert validation["status"] == "retry"
    check = next(c for c in validation["semantic_checks"] if c["name"] == "avoided_phrases_absent")
    assert check["passed"] is False
    assert "\u0442\u044b \u0432\u0441\u0435\u0433\u0434\u0430" in check["details"]
    assert any("\u0442\u044b \u0432\u0441\u0435\u0433\u0434\u0430" in item for item in validation["retry_instructions"])


# ------------------------------------------------------------------ helper


def _rules(*items: tuple[str, str]) -> list[RelationshipRule]:
    return [RelationshipRule(type=rule_type, value=value) for rule_type, value in items]


def test_avoided_phrases_only_from_quoted_text_of_avoid_rules() -> None:
    rules = _rules(("avoid", AVOID_RULE), ("tone", "'\u0442\u044b \u0434\u043e\u043b\u0436\u0435\u043d'"), ("avoid", "\u0431\u0435\u0437 \u0441\u0430\u0440\u043a\u0430\u0437\u043c\u0430"))
    assert avoided_phrases(rules) == ["\u0442\u044b \u0432\u0441\u0435\u0433\u0434\u0430", "\u0442\u044b \u043d\u0438\u043a\u043e\u0433\u0434\u0430"]


@pytest.mark.parametrize(
    ("rule", "text", "expected"),
    [
        ("\u0438\u0437\u0431\u0435\u0433\u0430\u0442\u044c \u00ab\u0442\u044b \u0432\u0441\u0435\u0433\u0434\u0430\u00bb", "\u0422\u044b   \u0432\u0441\u0435\u0433\u0434\u0430 \u0442\u0430\u043a", ["\u0442\u044b \u0432\u0441\u0435\u0433\u0434\u0430"]),
        ("\u0438\u0437\u0431\u0435\u0433\u0430\u0442\u044c \"\u0435\u0449\u0451\"", "\u0415\u0429\u0415 \u0440\u0430\u0437", ["\u0435\u0449\u0435"]),
        ("\u0438\u0437\u0431\u0435\u0433\u0430\u0442\u044c '\u0442\u044b'", "\u0442\u044b\u043a\u0432\u0430 \u0438 \u0442\u044b\u0441\u044f\u0447\u0430", []),
        (AVOID_RULE, GOOD, []),
    ],
)
def test_find_avoided_phrases_matches_whole_words_case_insensitive(rule: str, text: str, expected: list[str]) -> None:
    assert find_avoided_phrases(text, _rules(("avoid", rule))) == expected


def test_relationship_context_without_avoid_rules_has_no_checks() -> None:
    context = RelationshipContext(request_id="11111111-1111-4111-8111-111111111111", rules=_rules(("tone", TONE_RULE)))
    assert find_avoided_phrases(BAD, context.rules) == []


# ------------------------------------------------------------- retry loop


@pytest.mark.asyncio
async def test_first_generation_has_no_retry_instructions() -> None:
    provider = RecordingProvider([GOOD])
    engine, _ = make_engine(provider)
    await engine.execute(WorkflowName.SOFTEN, soften_request())

    assert len(provider.requests) == 1
    assert "retry_instructions" not in provider.requests[0].user_payload


@pytest.mark.asyncio
async def test_retry_generation_receives_exact_validation_retry_instructions() -> None:
    provider = RecordingProvider([BAD, GOOD])
    engine, checkpoints = make_engine(provider)
    result = await engine.execute(WorkflowName.SOFTEN, soften_request())

    assert len(provider.requests) == 2
    first, second = (r.user_payload for r in provider.requests)
    assert "retry_instructions" not in first
    assert second["retry_instructions"] == [
        "Regenerate and satisfy all failed semantic checks.",
        "Rewrite contains avoided phrases: \u0442\u044b \u0432\u0441\u0435\u0433\u0434\u0430",
    ]
    # Kept separate from relationship rules and plan constraints.
    assert [rule["value"] for rule in second["relationship_context"]["rules"]] == [AVOID_RULE, TONE_RULE]
    assert not any("Regenerate" in item for item in second["generation_plan"]["constraints"])

    assert result.status == "ok"
    assert result.text == GOOD
    assert "Regenerate" not in result.text
    assert result.structured_result is not None
    assert "retry_instructions" not in result.structured_result


class CorrectingProvider(StructuredLLMProvider):
    """Violates the avoid rule until it is given retry instructions, then corrects itself."""

    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, request: StructuredGenerationRequest) -> StructuredGenerationResponse:
        self.calls += 1
        corrected = bool(request.user_payload.get("retry_instructions"))
        return StructuredGenerationResponse(
            payload={
                "version": "1.0",
                "request_id": request.user_payload["message_request"]["request_id"],
                "original_intent": "intent",
                "rewritten_message": GOOD if corrected else BAD,
                "tone_applied": "direct",
                "constraints_respected": [AVOID_RULE] if corrected else [],
            },
            provider="correcting",
            model="test",
        )


@pytest.mark.asyncio
async def test_provider_corrects_output_using_retry_instructions() -> None:
    provider = CorrectingProvider()
    engine, _ = make_engine(provider)
    result = await engine.execute(WorkflowName.SOFTEN, soften_request())

    assert provider.calls == 2
    assert result.status == "ok"
    assert result.text == GOOD
    assert result.structured_result is not None
    assert result.structured_result["constraints_respected"] == [AVOID_RULE]


@pytest.mark.asyncio
async def test_retry_count_still_limited_by_workflow_yaml() -> None:
    provider = RecordingProvider([BAD])
    engine, _ = make_engine(provider)
    result = await engine.execute(WorkflowName.SOFTEN, soften_request())

    assert len(provider.requests) == 2  # retry.max_attempts in config/workflows/soften.yaml
    assert result.status == "error"


class PayloadRecordingFake(FakeStructuredLLMProvider):
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    async def generate(self, request: StructuredGenerationRequest) -> StructuredGenerationResponse:
        self.payloads.append(request.user_payload)
        return await super().generate(request)


@pytest.mark.parametrize("workflow", [WorkflowName.DECODE, WorkflowName.HELP_SAY])
@pytest.mark.asyncio
async def test_decode_and_help_say_unchanged(workflow: WorkflowName) -> None:
    provider = PayloadRecordingFake()
    engine, _ = make_engine(provider)
    result = await engine.execute(workflow, soften_request())

    assert result.status == "ok"
    assert len(provider.payloads) == 1
    assert "retry_instructions" not in provider.payloads[0]


# ------------------------------------------------------- one-shot retry instructions

EXPECTED_RETRY = [
    "Regenerate and satisfy all failed semantic checks.",
    "Rewrite contains avoided phrases: \u0442\u044b \u0432\u0441\u0435\u0433\u0434\u0430",
] 


class ScriptedProvider(RecordingProvider):
    """Each script item is a rewrite string or an exception to raise."""

    def __init__(self, script: list) -> None:
        super().__init__([])
        self.script = script

    async def generate(self, request: StructuredGenerationRequest) -> StructuredGenerationResponse:
        step = self.script[len(self.requests)]
        if isinstance(step, Exception):
            self.requests.append(request)
            raise step
        self.rewrites = [step]
        return await RecordingProvider.generate(self, request)
async def _run_failing_retry() -> tuple[WorkflowEngine, InMemoryCheckpointStore, ScriptedProvider, object]:
    provider = ScriptedProvider([BAD, RuntimeError("provider down"), GOOD])
    engine, checkpoints = make_engine(provider)
    with pytest.raises(RuntimeError, match="provider down"):
        await engine.execute(WorkflowName.SOFTEN, soften_request())
    request_id = next(iter(checkpoints._items))  # test-only inspection of in-memory adapter
    return engine, checkpoints, provider, request_id


@pytest.mark.asyncio
async def test_retry_instructions_survive_failed_retry_generation() -> None:
    _, checkpoints, provider, request_id = await _run_failing_retry()

    assert provider.requests[1].user_payload["retry_instructions"] == EXPECTED_RETRY
    failed = await checkpoints.load(request_id)
    assert failed is not None
    assert failed.stage == "generate"
    assert failed.status == "failed"
    assert failed.state["metadata"]["retry_instructions"] == EXPECTED_RETRY


@pytest.mark.asyncio
async def test_resume_after_failed_retry_receives_same_instructions() -> None:
    engine, checkpoints, provider, request_id = await _run_failing_retry()

    result = await engine.resume(request_id)

    assert len(provider.requests) == 3
    assert provider.requests[2].user_payload["retry_instructions"] == EXPECTED_RETRY
    assert result.status == "ok"
    assert result.text == GOOD


@pytest.mark.asyncio
async def test_retry_instructions_removed_after_successful_generation() -> None:
    provider = RecordingProvider([BAD, GOOD])
    engine, checkpoints = make_engine(provider)
    result = await engine.execute(WorkflowName.SOFTEN, soften_request())

    assert result.status == "ok"
    snapshot = next(iter(checkpoints._items.values()))  # test-only inspection
    assert snapshot.status == "completed"
    assert "retry_instructions" not in snapshot.state["metadata"]


@pytest.mark.asyncio
async def test_later_generation_cannot_receive_stale_instructions() -> None:
    from types import SimpleNamespace

    from app.config import load_workflow_manifest
    from app.stages.generation import GenerationStage

    provider = RecordingProvider([BAD, GOOD])
    engine, checkpoints = make_engine(provider)
    await engine.execute(WorkflowName.SOFTEN, soften_request())
    state, _ = WorkflowEngine._restore_state(next(iter(checkpoints._items.values())))

    tools = ToolRegistry()
    tools.register(LLMGenerateTool(provider))
    context = SimpleNamespace(skill_loader=SkillLoader(), tools=tools)
    generate = next(s for s in load_workflow_manifest("soften").stages if s.name == "generate")
    stage = GenerationStage()

    state.metadata["retry_instructions"] = ["one-shot"]
    await stage.execute(state, generate, context)
    assert provider.requests[-1].user_payload["retry_instructions"] == ["one-shot"]
    assert "retry_instructions" not in state.metadata

    await stage.execute(state, generate, context)
    assert "retry_instructions" not in provider.requests[-1].user_payload
