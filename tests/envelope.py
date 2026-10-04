from __future__ import annotations

from typing import Any

from app.artifacts.internal import GenerationEnvelope
from app.tools.llm.base import StructuredGenerationRequest

PASSING_SELF_CHECK: dict[str, Any] = {"all_passed": True, "failures": [], "violated_rule_ids": []}


def wants_envelope(request: StructuredGenerationRequest) -> bool:
    return isinstance(request.output_model, type) and issubclass(request.output_model, GenerationEnvelope)


def wrap(
    request: StructuredGenerationRequest,
    payload: dict[str, Any],
    self_check: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the strict envelope when requested; plain payload otherwise."""
    if not wants_envelope(request):
        return payload
    return {"result": payload, "self_check": self_check if self_check is not None else PASSING_SELF_CHECK}
