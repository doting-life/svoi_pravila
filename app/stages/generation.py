from __future__ import annotations

import time

from app.artifacts import Artifact, GenerationPlan, RelationshipContext
from app.config import StageManifest
from app.stages.base import BaseStage, StageContext
from app.stages.self_check import relationship_payload, system_contract, trace_summary
from app.workflows.state import WorkflowState


class GenerationStage(BaseStage):
    async def execute(self, state: WorkflowState, manifest: StageManifest, context: StageContext) -> Artifact:
        started = time.monotonic()
        plan = state.artifacts["generation_plan"]
        assert isinstance(plan, GenerationPlan)

        bundle = context.skill_loader.load_bundle(plan.skill_name)
        # Trusted: skill text + generation contract + workflow policies.
        system_instructions = "\n\n".join(
            [*(skill.body for skill in bundle), system_contract(context.workflow_manifest.policies)]
        )
        # Untrusted data only (source, language, relationship context/rules, plan constraints).
        user_payload = {
            name: artifact.model_dump(mode="json")
            for name, artifact in state.require(manifest.requires).items()
        }
        relationship = state.artifacts.get("relationship_context")
        if isinstance(relationship, RelationshipContext) and "relationship_context" in user_payload:
            user_payload["relationship_context"] = relationship_payload(relationship)

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
                "ruleset_version": getattr(relationship, "ruleset_version", None),
            },
            self_check=True,
            request_id=state.request_id,
        )
        if not result.success:
            raise RuntimeError(result.error or "LLM generation failed")
        state.metadata.pop("retry_instructions", None)

        llm = dict(result.metadata or {})
        check = llm.pop("self_check", None)
        status = llm.pop("self_check_status", "missing")
        state.metadata["self_check"] = check.model_dump(mode="json") if check is not None else None
        state.metadata["self_check_status"] = status
        state.metadata["self_check_mode"] = getattr(tool, "self_check_mode", "required")
        llm.update(trace_summary(check, status))
        llm["relationship_id"] = getattr(relationship, "relationship_id", None)
        llm["ruleset_version"] = getattr(relationship, "ruleset_version", None)
        llm["generation_ms"] = int((time.monotonic() - started) * 1000)
        state.metadata["llm"] = llm
        return result.data
