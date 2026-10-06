from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class WorkflowName(StrEnum):
    SOFTEN = "soften"
    DECODE = "decode"
    HELP_SAY = "help-say"


class Artifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = "1.0"
    request_id: UUID


class AssistRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    text: str = Field(min_length=1, max_length=10_000)
    relationship_id: str | None = None
    language: str = "ru"


class MessageRequest(Artifact):
    user_id: str
    workflow: WorkflowName
    text: str = Field(min_length=1, max_length=10_000)
    relationship_id: str | None = None
    language: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def from_api(cls, request: AssistRequest, workflow: WorkflowName) -> "MessageRequest":
        return cls(
            request_id=uuid4(),
            user_id=request.user_id,
            workflow=workflow,
            text=request.text,
            relationship_id=request.relationship_id,
            language=request.language,
        )


class SafetyDecision(Artifact):
    status: Literal["allow", "allow_with_constraints", "block"]
    categories: list[str] = Field(default_factory=list)
    instructions: list[str] = Field(default_factory=list)
    user_message: str | None = None


class RelationshipRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    type: str
    value: str
    priority: int = 0
    created_at: datetime | None = None


class RelationshipContext(Artifact):
    relationship_id: str | None = None
    relation_type: str | None = None
    aliases: list[str] = Field(default_factory=list)
    rules: list[RelationshipRule] = Field(default_factory=list)
    communication_style: dict[str, Any] = Field(default_factory=dict)
    ruleset_version: int = Field(default=0, ge=0)


class GenerationPlan(Artifact):
    workflow: WorkflowName
    skill_name: str
    skill_version: str
    objective: str
    tone: str | None = None
    constraints: list[str] = Field(default_factory=list)
    output_schema: str
    provider_preferences: dict[str, Any] = Field(default_factory=dict)


class SoftenResult(Artifact):
    original_intent: str
    rewritten_message: str = Field(min_length=1)
    tone_applied: str
    constraints_respected: list[str] = Field(default_factory=list)


class DecodeResult(Artifact):
    literal_meaning: str
    probable_intent: str
    emotional_tone: str
    uncertainty: str
    alternative_interpretations: list[str] = Field(default_factory=list)


class HelpSayResult(Artifact):
    message: str = Field(min_length=1)
    tone: str
    preserved_intent: str = Field(min_length=1)
    warnings: list[str] = Field(default_factory=list)


WorkflowResult = SoftenResult | DecodeResult | HelpSayResult


class SemanticCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    passed: bool
    details: str | None = None


class ValidationResult(Artifact):
    status: Literal["pass", "retry", "block"]
    schema_valid: bool
    policy_valid: bool
    language_valid: bool
    semantic_checks: list[SemanticCheck] = Field(default_factory=list)
    retry_instructions: list[str] = Field(default_factory=list)


class DeliveryResponse(Artifact):
    workflow: WorkflowName
    status: Literal["ok", "blocked", "error"]
    text: str
    structured_result: dict[str, Any] | None = None


ARTIFACT_MODELS: dict[str, type[BaseModel]] = {
    "message_request": MessageRequest,
    "safety_decision": SafetyDecision,
    "relationship_context": RelationshipContext,
    "generation_plan": GenerationPlan,
    "soften_result": SoftenResult,
    "decode_result": DecodeResult,
    "help_say_result": HelpSayResult,
    "validation_result": ValidationResult,
    "delivery_response": DeliveryResponse,
}


def validate_artifact(name: str, value: Any) -> BaseModel:
    try:
        model = ARTIFACT_MODELS[name]
    except KeyError as exc:
        raise ValueError(f"Unknown artifact: {name}") from exc
    if isinstance(value, model):
        return value
    return model.model_validate(value)
