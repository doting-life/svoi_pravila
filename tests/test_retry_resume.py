from __future__ import annotations

import pytest

from app.artifacts import AssistRequest, SoftenResult, WorkflowName
from app.checkpoints import InMemoryCheckpointStore
from app.observability import RequestTraceSink
from app.repositories import InMemoryRelationshipRepository
from app.skills import SkillLoader
from app.stages import StageRegistry
from app.tools.llm import LLMGenerateTool
from app.tools.llm.base import StructuredGenerationRequest, StructuredGenerationResponse, StructuredLLMProvider
from app.tools.registry import ToolRegistry
from app.workflows.engine import WorkflowDependencies, WorkflowEngine
from tests.envelope import wrap


class RetryOnceProvider(StructuredLLMProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, request: StructuredGenerationRequest) -> StructuredGenerationResponse:
        self.calls += 1
        source = request.user_payload["message_request"]["text"]
        request_id = request.user_payload["message_request"]["request_id"]
        rewritten = " " if self.calls == 1 else "Мне важно, чтобы это было сделано."
        return StructuredGenerationResponse(
            payload=wrap(
                request,
                {
                    "version": "1.0",
                    "request_id": request_id,
                    "original_intent": source,
                    "rewritten_message": rewritten,
                    "tone_applied": "calm_direct",
                    "constraints_respected": [],
                },
            ),
            provider="retry-once",
            model="test",
        )


class FailOnceProvider(StructuredLLMProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, request: StructuredGenerationRequest) -> StructuredGenerationResponse:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("temporary provider failure")
        source = request.user_payload["message_request"]["text"]
        request_id = request.user_payload["message_request"]["request_id"]
        return StructuredGenerationResponse(
            payload=wrap(request, {
                "version": "1.0",
                "request_id": request_id,
                "original_intent": source,
                "rewritten_message": "Мне важно, чтобы это было сделано.",
                "tone_applied": "calm_direct",
                "constraints_respected": [],
            }),
            provider="fail-once",
            model="test",
        )


def make_engine(provider: StructuredLLMProvider):
    tools = ToolRegistry()
    tools.register(LLMGenerateTool(provider))
    checkpoints = InMemoryCheckpointStore()
    trace = RequestTraceSink()
    engine = WorkflowEngine(
        WorkflowDependencies(
            skills=SkillLoader(),
            tools=tools,
            relationships=InMemoryRelationshipRepository.demo(),
            checkpoints=checkpoints,
            trace=trace,
            stages=StageRegistry(),
        )
    )
    return engine, checkpoints, trace


@pytest.mark.asyncio
async def test_validation_can_retry_only_declared_generate_stage() -> None:
    provider = RetryOnceProvider()
    engine, checkpoints, trace = make_engine(provider)
    result = await engine.execute(
        WorkflowName.SOFTEN,
        AssistRequest(user_id="u-1", text="Ты опять ничего не сделал", language="ru"),
    )
    assert result.status == "ok"
    assert provider.calls == 2
    generate_events = [event for event in trace.events if event.stage == "generate"]
    assert [event.attempt for event in generate_events] == [1, 2]


@pytest.mark.asyncio
async def test_failed_stage_can_resume_from_checkpoint() -> None:
    provider = FailOnceProvider()
    engine, checkpoints, _ = make_engine(provider)

    with pytest.raises(RuntimeError, match="temporary provider failure"):
        await engine.execute(
            WorkflowName.SOFTEN,
            AssistRequest(user_id="u-1", text="Ты опять ничего не сделал", language="ru"),
        )

    assert len(checkpoints._items) == 1  # test-only inspection of in-memory adapter
    request_id = next(iter(checkpoints._items))
    failed = await checkpoints.load(request_id)
    assert failed is not None
    assert failed.stage == "generate"
    assert failed.status == "failed"

    result = await engine.resume(request_id)
    assert result.status == "ok"
    assert provider.calls == 2
