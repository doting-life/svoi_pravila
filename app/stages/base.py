from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.artifacts import Artifact, AssistRequest, WorkflowName
from app.config import StageManifest, WorkflowManifest
from app.observability import TraceSink
from app.repositories import RelationshipRepository, UserRepository
from app.skills import SkillLoader
from app.tools.registry import ToolRegistry
from app.workflows.state import WorkflowState


@dataclass(slots=True)
class StageContext:
    api_request: AssistRequest
    workflow_name: WorkflowName
    workflow_manifest: WorkflowManifest
    skill_loader: SkillLoader
    tools: ToolRegistry
    relationships: RelationshipRepository
    users: UserRepository
    trace: TraceSink


class BaseStage(ABC):
    @abstractmethod
    async def execute(
        self,
        state: WorkflowState,
        manifest: StageManifest,
        context: StageContext,
    ) -> Artifact:
        raise NotImplementedError
