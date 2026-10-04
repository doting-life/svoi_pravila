from __future__ import annotations

import time

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
from app.stages.self_check import retry_instructions as self_check_retry_instructions
from app.stages.self_check import rule_ids, self_check_failed
from app.artifacts.internal import SelfCheck
from app.workflows.state import WorkflowState


class ValidationStage(BaseStage):
    async def execute(self, state: WorkflowState, manifest: StageManifest, context: StageContext) -> ValidationResult:
        started = time.monotonic()
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
            # Frozen temporary regression guard, Russian variants only (see commitments.py).
            invented = (
                find_invented_commitments(result.message, request.text) if _is_russian(request.language) else []
            )
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

        # Model self-check: structured evidence. An explicit failure triggers retry;
        # a pass never overrides the deterministic checks above.
        self_check_instructions: list[str] = []
        raw_check = state.metadata.get("self_check")
        if raw_check is not None:
            check = SelfCheck.model_validate(raw_check)
            failed = self_check_failed(check)
            checks.append(SemanticCheck(name="self_check", passed=not failed))
            if failed:
                relationship = state.artifacts.get("relationship_context")
                known = rule_ids(relationship) if isinstance(relationship, RelationshipContext) else []
                self_check_instructions = self_check_retry_instructions(check, known)

        # Required mode: a missing/malformed self_check blocks delivery without a
        # retry (a retry would cost another provider call on the latency budget).
        self_check_absent = (
            state.metadata.get("self_check_mode") == "required"
            and state.metadata.get("self_check_status") in ("missing", "malformed")
        )
        if self_check_absent:
            checks.append(SemanticCheck(name="self_check_present", passed=False))

        schema_valid = True
        policy_valid = all(check.passed for check in checks)
        language_valid = True
        if self_check_absent:
            status = "block"
            retry_instructions: list[str] = []
        else:
            status = "pass" if schema_valid and policy_valid and language_valid else "retry"
            retry_instructions = [] if status == "pass" else ["Regenerate and satisfy all failed semantic checks."]
            retry_instructions.extend(check.details for check in checks if not check.passed and check.details)
            retry_instructions.extend(self_check_instructions)

        state.metadata["validation"] = {
            "validation_ms": int((time.monotonic() - started) * 1000),
            "validation_status": status,
            "failed_checks": [check.name for check in checks if not check.passed],
            "check_count": len(checks),
            "self_check_status": state.metadata.get("self_check_status"),
        }

        return ValidationResult(
            request_id=state.request_id,
            status=status,
            schema_valid=schema_valid,
            policy_valid=policy_valid,
            language_valid=language_valid,
            semantic_checks=checks,
            retry_instructions=retry_instructions,
        )


def _is_russian(language: str) -> bool:
    return language.strip().lower().replace("_", "-").split("-")[0] == "ru"
