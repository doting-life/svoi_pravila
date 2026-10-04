"""Internal generation contract types.

These models are exchanged only between the LLM provider and the workflow
stages. They are never part of the public API, ``ARTIFACT_MODELS`` or
``DeliveryResponse``.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, ConfigDict, create_model

SelfCheckCode = Literal[
    "intent_preserved",
    "invented_facts",
    "invented_emotions",
    "invented_commitments",
    "relationship_rules_respected",
    "workflow_policy_respected",
]

SELF_CHECK_CODES: tuple[str, ...] = SelfCheckCode.__args__  # type: ignore[attr-defined]


class SelfCheckFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: SelfCheckCode
    rule_id: str | None = None


class SelfCheck(BaseModel):
    """Model-reported semantic self-check. Structured evidence, not proof."""

    model_config = ConfigDict(extra="forbid")

    all_passed: bool
    failures: list[SelfCheckFailure]
    violated_rule_ids: list[str]


class GenerationEnvelope(BaseModel):
    """Provider output: the public workflow result plus the internal self-check."""

    model_config = ConfigDict(extra="forbid")

    self_check: SelfCheck


@lru_cache(maxsize=None)
def envelope_for(result_model: type[BaseModel]) -> type[GenerationEnvelope]:
    """Strict provider schema: ``{"result": <result_model>, "self_check": SelfCheck}``."""
    return create_model(
        f"{result_model.__name__}Envelope",
        __base__=GenerationEnvelope,
        result=(result_model, ...),
    )
