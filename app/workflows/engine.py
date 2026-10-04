from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from app.artifacts import (
    AssistRequest,
    DeliveryResponse,
    SafetyDecision,
    WorkflowName,
    validate_artifact,
)
from app.checkpoints import CheckpointSnapshot, CheckpointStore
from app.config import WorkflowManifest, load_workflow_manifest
from app.observability import RequestTraceSink, TraceEvent
from app.repositories import InMemoryUserRepository, RelationshipRepository, UserRepository
from app.skills import SkillLoader
from app.stages import StageContext, StageRegistry
from app.tools.registry import ToolRegistry
from app.workflows.state import WorkflowState


@dataclass(slots=True)
class WorkflowDependencies:
    skills: SkillLoader
    tools: ToolRegistry
    relationships: RelationshipRepository
    checkpoints: CheckpointStore
    trace: RequestTraceSink
    stages: StageRegistry
    users: UserRepository = field(default_factory=InMemoryUserRepository)


class WorkflowEngine:
    def __init__(self, dependencies: WorkflowDependencies) -> None:
        self.dependencies = dependencies

    async def execute(self, workflow: WorkflowName, request: AssistRequest) -> DeliveryResponse:
        manifest = load_workflow_manifest(workflow.value)
        state = WorkflowState(request_id=uuid4(), workflow=workflow.value)
        return await self._run(manifest, request, state, start_index=0)

    async def resume(self, request_id: UUID) -> DeliveryResponse:
        snapshot = await self.dependencies.checkpoints.load(request_id)
        if snapshot is None:
            raise KeyError(f"No checkpoint for request {request_id}")
        manifest = load_workflow_manifest(snapshot.workflow)
        state, api_request = self._restore_state(snapshot)

        if snapshot.status == "blocked" and "delivery_response" in state.artifacts:
            result = state.artifacts["delivery_response"]
            assert isinstance(result, DeliveryResponse)
            return result

        stage_names = [stage.name for stage in manifest.stages]
        try:
            index = stage_names.index(snapshot.stage)
        except ValueError as exc:
            raise ValueError(f"Checkpoint stage {snapshot.stage!r} is not in workflow") from exc
        start_index = index + 1 if snapshot.status == "completed" else index
        return await self._run(manifest, api_request, state, start_index=start_index)

    async def _run(
        self,
        manifest: WorkflowManifest,
        api_request: AssistRequest,
        state: WorkflowState,
        *,
        start_index: int,
    ) -> DeliveryResponse:
        context = StageContext(
            api_request=api_request,
            workflow_name=WorkflowName(manifest.name),
            workflow_manifest=manifest,
            skill_loader=self.dependencies.skills,
            tools=self.dependencies.tools,
            relationships=self.dependencies.relationships,
            users=self.dependencies.users,
            trace=self.dependencies.trace,
        )

        index = start_index
        while index < len(manifest.stages):
            stage_manifest = manifest.stages[index]
            state.require(stage_manifest.requires)
            stage = self.dependencies.stages.resolve(stage_manifest.handler)
            attempt = state.stage_attempts.get(stage_manifest.name, 0) + 1
            state.stage_attempts[stage_manifest.name] = attempt

            await self._checkpoint(state, api_request, stage_manifest.name, "in_progress", attempt)
            started = time.monotonic()
            try:
                artifact = await stage.execute(state, stage_manifest, context)
                artifact = validate_artifact(stage_manifest.produces, artifact)
                state.put(stage_manifest.produces, artifact)
            except Exception as exc:
                await self._checkpoint(state, api_request, stage_manifest.name, "failed", attempt)
                await self._trace(
                    state,
                    stage_manifest.name,
                    "failed",
                    attempt,
                    started,
                    {"error_type": type(exc).__name__},
                )
                raise

            await self._trace(
                state,
                stage_manifest.name,
                "completed",
                attempt,
                started,
                state.metadata.get("llm", {}) if stage_manifest.name == "generate" else {},
            )

            if stage_manifest.name == "safety" and isinstance(artifact, SafetyDecision) and artifact.status == "block":
                delivery = DeliveryResponse(
                    request_id=state.request_id,
                    workflow=WorkflowName(state.workflow),
                    status="blocked",
                    text=artifact.user_message or "Этот запрос нельзя обработать в обычном режиме.",
                    structured_result=None,
                )
                state.put("delivery_response", delivery)
                await self._checkpoint(state, api_request, stage_manifest.name, "blocked", attempt)
                return delivery

            await self._checkpoint(state, api_request, stage_manifest.name, "completed", attempt)

            if stage_manifest.name == "validate" and getattr(artifact, "status", None) == "retry":
                retry = stage_manifest.retry
                if retry and retry.retry_stage:
                    retry_attempts = state.stage_attempts.get(retry.retry_stage, 0)
                    if retry_attempts < retry.max_attempts:
                        retry_index = self._stage_index(manifest, retry.retry_stage)
                        state.metadata["retry_instructions"] = list(getattr(artifact, "retry_instructions", []))
                        self._discard_from(state, manifest, retry_index)
                        index = retry_index
                        continue

            index += 1

        result = state.artifacts.get("delivery_response")
        if not isinstance(result, DeliveryResponse):
            raise RuntimeError("Workflow completed without delivery_response")
        return result

    async def _checkpoint(
        self,
        state: WorkflowState,
        api_request: AssistRequest,
        stage: str,
        status: str,
        attempt: int,
    ) -> None:
        await self.dependencies.checkpoints.save(
            CheckpointSnapshot(
                request_id=state.request_id,
                workflow=state.workflow,
                stage=stage,
                status=status,  # type: ignore[arg-type]
                attempt=attempt,
                artifact_names=sorted(state.artifacts),
                state=self._serialize_state(state, api_request),
                updated_at=datetime.now(timezone.utc),
            )
        )

    async def _trace(
        self,
        state: WorkflowState,
        stage: str,
        status: str,
        attempt: int,
        started: float,
        metadata: dict[str, Any],
    ) -> None:
        latency_ms = int((time.monotonic() - started) * 1000)
        await self.dependencies.trace.record(
            TraceEvent(
                request_id=state.request_id,
                workflow=state.workflow,
                stage=stage,
                status=status,
                attempt=attempt,
                latency_ms=latency_ms,
                metadata=metadata,
            )
        )

    @staticmethod
    def _stage_index(manifest: WorkflowManifest, name: str) -> int:
        for index, stage in enumerate(manifest.stages):
            if stage.name == name:
                return index
        raise ValueError(f"Retry stage {name!r} does not exist in workflow {manifest.name!r}")

    @staticmethod
    def _discard_from(state: WorkflowState, manifest: WorkflowManifest, start_index: int) -> None:
        for stage in manifest.stages[start_index:]:
            state.artifacts.pop(stage.produces, None)

    @staticmethod
    def _serialize_state(state: WorkflowState, api_request: AssistRequest) -> dict[str, Any]:
        return {
            "request_id": str(state.request_id),
            "workflow": state.workflow,
            "artifacts": {
                name: artifact.model_dump(mode="json")
                for name, artifact in state.artifacts.items()
            },
            "stage_attempts": dict(state.stage_attempts),
            "metadata": dict(state.metadata),
            "api_request": api_request.model_dump(mode="json"),
        }

    @staticmethod
    def _restore_state(snapshot: CheckpointSnapshot) -> tuple[WorkflowState, AssistRequest]:
        raw = snapshot.state
        state = WorkflowState(
            request_id=UUID(raw["request_id"]),
            workflow=raw["workflow"],
            stage_attempts=dict(raw.get("stage_attempts", {})),
            metadata=dict(raw.get("metadata", {})),
        )
        for name, value in raw.get("artifacts", {}).items():
            state.put(name, validate_artifact(name, value))
        api_request = AssistRequest.model_validate(raw["api_request"])
        return state, api_request
