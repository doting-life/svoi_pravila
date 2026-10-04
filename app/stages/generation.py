from __future__ import annotations

from app.artifacts import Artifact, GenerationPlan
from app.config import StageManifest
from app.stages.base import BaseStage, StageContext
from app.workflows.state import WorkflowState


class GenerationStage(BaseStage):
    async def execute(self, state: WorkflowState, manifest: StageManifest, context: StageContext) -> Artifact:
        plan = state.artifacts["generation_plan"]
        assert isinstance(plan, GenerationPlan)

        bundle = context.skill_loader.load_bundle(plan.skill_name)
        system_instructions = "\n\n".join(skill.body for skill in bundle)
        user_payload = {
            name: artifact.model_dump(mode="json")
            for name, artifact in state.require(manifest.requires).items()
        }

        retry_instructions = state.metadata.get("retry_instructions")
        if retry_instructions:
            user_payload["retry_instructions"] = list(retry_instructions)

        tool = context.tools.get("llm_generate")
        result = await tool.execute(
            system_instructions=system_instructions,
            user_payload=user_payload,
            output_artifact=plan.output_schema,
            metadata={
                "workflow": state.workflow,
                "skill_name": plan.skill_name,
                "skill_version": plan.skill_version,
                "ruleset_version": getattr(state.artifacts.get("relationship_context"), "ruleset_version", None),
            },
        )
        if not result.success:
            raise RuntimeError(result.error or "LLM generation failed")
        state.metadata.pop("retry_instructions", None)
        state.metadata["llm"] = result.metadata or {}
        return result.data
