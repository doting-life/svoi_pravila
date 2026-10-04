import pytest

from app.artifacts import AssistRequest, WorkflowName
from app.container import build_container


@pytest.mark.parametrize("workflow", list(WorkflowName))
@pytest.mark.asyncio
async def test_all_workflows_run_end_to_end(workflow: WorkflowName) -> None:
    container = build_container()
    result = await container.engine.execute(
        workflow,
        AssistRequest(
            user_id="u-1",
            relationship_id="partner-1",
            text="Ты опять ничего не сделал",
            language="ru",
        ),
    )

    assert result.status == "ok"
    assert result.text.strip()
    checkpoint = await container.checkpoints.load(result.request_id)
    assert checkpoint is not None
    assert checkpoint.stage == "deliver"
    assert checkpoint.status == "completed"
    assert "delivery_response" in checkpoint.artifact_names

    events = [event for event in container.trace.events if event.request_id == result.request_id]
    assert [event.stage for event in events] == [
        "receive",
        "safety",
        "context",
        "plan",
        "generate",
        "validate",
        "deliver",
        "request",
    ]
    assert all("text" not in event.metadata for event in events)
    assert all("raw_prompt" not in event.metadata for event in events)
    by_stage = {event.stage: event for event in events}
    assert "provider_latency_ms" in by_stage["generate"].metadata
    assert "generation_ms" in by_stage["generate"].metadata
    assert "validation_ms" in by_stage["validate"].metadata
    assert by_stage["request"].latency_ms is not None


@pytest.mark.asyncio
async def test_relationship_rules_are_loaded_into_generation_plan() -> None:
    container = build_container()
    result = await container.engine.execute(
        WorkflowName.SOFTEN,
        AssistRequest(
            user_id="u-1",
            relationship_id="partner-1",
            text="Ты всегда всё откладываешь",
            language="ru",
        ),
    )
    checkpoint = await container.checkpoints.load(result.request_id)
    assert checkpoint is not None
    plan = checkpoint.state["artifacts"]["generation_plan"]
    assert any("категоричные" in item for item in plan["constraints"])
