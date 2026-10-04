from __future__ import annotations

from typing import Any

import pytest

from app.artifacts import AssistRequest, WorkflowName
from app.tools.llm.base import StructuredGenerationRequest, StructuredGenerationResponse, StructuredLLMProvider
from tests.envelope import PASSING_SELF_CHECK
from tests.test_self_check import (
    COMMITMENT_FAILURE,
    EN_BAD,
    EN_GOOD,
    EN_SOURCE,
    CountingFakeProvider,
    ScriptedProvider,
    en_request,
    make_engine,
)

MALFORMED = {"all_passed": "maybe"}


def request_events(trace: Any) -> list[Any]:
    return [event for event in trace.events if event.stage == "request"]


def assert_private(trace: Any) -> None:
    dumped = repr([event.metadata for event in trace.events])
    for raw in (EN_SOURCE, EN_GOOD, EN_BAD):
        assert raw not in dumped


@pytest.mark.asyncio
async def test_success_emits_exactly_one_request_trace() -> None:
    provider = CountingFakeProvider()
    engine, trace = make_engine(provider)
    result = await engine.execute(WorkflowName.SOFTEN, AssistRequest(user_id="u-1", text="Ты опять опоздал.", language="ru"))
    assert result.status == "ok"
    assert len(provider.requests) == 1
    [event] = request_events(trace)
    assert event.status == "ok"
    assert event.latency_ms is not None
    assert event.metadata == {"generate_attempts": 1}


@pytest.mark.asyncio
async def test_safety_block_emits_one_blocked_request_trace() -> None:
    from app.artifacts import SafetyDecision
    from app.stages.safety import SafetyStage

    class BlockingSafety(SafetyStage):
        async def execute(self, state, manifest, context):  # type: ignore[no-untyped-def]
            return SafetyDecision(request_id=state.request_id, status="block", categories=[], instructions=[])

    provider = CountingFakeProvider()
    engine, trace = make_engine(provider)
    engine.dependencies.stages._by_class_name["SafetyStage"] = BlockingSafety()
    result = await engine.execute(WorkflowName.SOFTEN, en_request())
    assert result.status == "blocked"
    assert provider.requests == []
    [event] = request_events(trace)
    assert event.status == "blocked"
    assert event.metadata == {"generate_attempts": 0}


@pytest.mark.asyncio
async def test_validation_exhausted_emits_one_error_request_trace() -> None:
    provider = ScriptedProvider([(EN_BAD, COMMITMENT_FAILURE)])
    engine, trace = make_engine(provider)
    result = await engine.execute(WorkflowName.HELP_SAY, en_request())
    assert result.status == "error"
    assert result.text != EN_BAD
    [event] = request_events(trace)
    assert event.status == "error"
    assert event.metadata["generate_attempts"] == len(provider.requests) > 1
    assert_private(trace)


class FailingProvider(StructuredLLMProvider):
    async def generate(self, request: StructuredGenerationRequest) -> StructuredGenerationResponse:
        raise RuntimeError(f"provider exploded on {EN_SOURCE}")


@pytest.mark.asyncio
async def test_provider_exception_emits_one_failed_request_trace() -> None:
    engine, trace = make_engine(FailingProvider())
    with pytest.raises(RuntimeError):
        await engine.execute(WorkflowName.HELP_SAY, en_request())
    [event] = request_events(trace)
    assert event.status == "failed"
    assert event.metadata == {"generate_attempts": 1, "error_type": "RuntimeError"}
    assert_private(trace)


class InvalidResultProvider(StructuredLLMProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, request: StructuredGenerationRequest) -> StructuredGenerationResponse:
        self.calls += 1
        return StructuredGenerationResponse(
            payload={"result": {"message": EN_GOOD}, "self_check": PASSING_SELF_CHECK},
            provider="invalid",
            model="test",
        )


@pytest.mark.asyncio
async def test_invalid_public_result_is_hard_failure_and_traced() -> None:
    provider = InvalidResultProvider()
    engine, trace = make_engine(provider)
    with pytest.raises(RuntimeError, match="generation contract failure: invalid result"):
        await engine.execute(WorkflowName.HELP_SAY, en_request())
    assert provider.calls == 1
    [event] = request_events(trace)
    assert event.status == "failed"
    assert event.metadata["error_type"] == "RuntimeError"
    assert_private(trace)


@pytest.mark.asyncio
@pytest.mark.parametrize("check", ["omit", MALFORMED])
async def test_required_missing_or_malformed_self_check_blocks_without_retry(check: Any) -> None:
    provider = ScriptedProvider([(EN_GOOD, check)])
    engine, trace = make_engine(provider, mode="required")
    result = await engine.execute(WorkflowName.HELP_SAY, en_request())
    assert len(provider.requests) == 1
    assert result.status == "error"
    assert result.text != EN_GOOD
    assert result.structured_result is None
    [event] = request_events(trace)
    assert event.status == "error"
    assert event.metadata == {"generate_attempts": 1}
    validate = next(e for e in trace.events if e.stage == "validate")
    assert validate.metadata["validation_status"] == "block"
    assert "self_check_present" in validate.metadata["failed_checks"]
    assert_private(trace)


@pytest.mark.asyncio
@pytest.mark.parametrize("check", ["omit", MALFORMED])
async def test_observe_missing_or_malformed_self_check_still_delivers(check: Any) -> None:
    provider = ScriptedProvider([(EN_GOOD, check)])
    engine, trace = make_engine(provider, mode="observe")
    result = await engine.execute(WorkflowName.HELP_SAY, en_request())
    assert len(provider.requests) == 1
    assert result.status == "ok"
    assert result.text == EN_GOOD
    [event] = request_events(trace)
    assert event.status == "ok"


@pytest.mark.asyncio
async def test_explicit_self_check_failure_still_retries_with_one_request_trace() -> None:
    provider = ScriptedProvider([(EN_BAD, COMMITMENT_FAILURE), (EN_GOOD, PASSING_SELF_CHECK)])
    engine, trace = make_engine(provider, mode="required")
    result = await engine.execute(WorkflowName.HELP_SAY, en_request())
    assert result.status == "ok"
    assert len(provider.requests) == 2
    [event] = request_events(trace)
    assert event.status == "ok"
    assert event.metadata == {"generate_attempts": 2}


@pytest.mark.asyncio
async def test_clean_required_request_makes_one_provider_call() -> None:
    provider = ScriptedProvider([(EN_GOOD, PASSING_SELF_CHECK)])
    engine, trace = make_engine(provider, mode="required")
    result = await engine.execute(WorkflowName.HELP_SAY, en_request())
    assert result.status == "ok"
    assert len(provider.requests) == 1
    assert len(request_events(trace)) == 1
