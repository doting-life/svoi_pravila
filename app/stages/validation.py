from __future__ import annotations

from app.artifacts import (
    DecodeResult,
    HelpSayResult,
    MessageRequest,
    RelationshipContext,
    SemanticCheck,
    SoftenResult,
    ValidationResult,
)
from app.config import StageManifest
from app.stages.base import BaseStage, StageContext
from app.stages.commitments import find_invented_commitments
from app.stages.constraints import find_avoided_phrases
from app.workflows.state import WorkflowState


class ValidationStage(BaseStage):
    async def execute(self, state: WorkflowState, manifest: StageManifest, context: StageContext) -> ValidationResult:
        request = state.artifacts["message_request"]
        assert isinstance(request, MessageRequest)

        result_name = next(
            name
            for name in ("soften_result", "decode_result", "help_say_result")
            if name in state.artifacts
        )
        result = state.artifacts[result_name]
        checks: list[SemanticCheck] = []

        if isinstance(result, SoftenResult):
            checks.append(SemanticCheck(name="non_empty_rewrite", passed=bool(result.rewritten_message.strip())))
            checks.append(SemanticCheck(name="intent_recorded", passed=bool(result.original_intent.strip())))
            relationship = state.artifacts.get("relationship_context")
            if isinstance(relationship, RelationshipContext):
                violations = find_avoided_phrases(result.rewritten_message, relationship.rules)
                checks.append(
                    SemanticCheck(
                        name="avoided_phrases_absent",
                        passed=not violations,
                        details=None if not violations else "Rewrite contains avoided phrases: " + ", ".join(violations),
                    )
                )
        elif isinstance(result, DecodeResult):
            checks.append(SemanticCheck(name="uncertainty_present", passed=bool(result.uncertainty.strip())))
            checks.append(SemanticCheck(name="probable_not_certain", passed=bool(result.probable_intent.strip())))
        elif isinstance(result, HelpSayResult):
            checks.append(SemanticCheck(name="non_empty_message", passed=bool(result.message.strip())))
            checks.append(SemanticCheck(name="intent_preserved", passed=bool(result.preserved_intent.strip())))
            invented = find_invented_commitments(result.message, request.text)
            checks.append(
                SemanticCheck(
                    name="invented_commitments_absent",
                    passed=not invented,
                    details=None
                    if not invented
                    else "Remove commitments or proposals the user did not ask for: "
                    + ", ".join(f"'{phrase}'" for phrase in invented)
                    + ". Do not promise or propose anything on the user's behalf.",
                )
            )

        schema_valid = True
        policy_valid = all(check.passed for check in checks)
        language_valid = True
        status = "pass" if schema_valid and policy_valid and language_valid else "retry"
        retry_instructions = [] if status == "pass" else ["Regenerate and satisfy all failed semantic checks."]
        retry_instructions.extend(check.details for check in checks if not check.passed and check.details)

        return ValidationResult(
            request_id=state.request_id,
            status=status,
            schema_valid=schema_valid,
            policy_valid=policy_valid,
            language_valid=language_valid,
            semantic_checks=checks,
            retry_instructions=retry_instructions,
        )
