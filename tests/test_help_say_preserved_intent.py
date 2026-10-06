from __future__ import annotations

import pytest

from app.artifacts import HelpSayResult, WorkflowName
from app.tools.llm.base import StructuredGenerationRequest, StructuredGenerationResponse
from tests.test_help_say_commitments import GOOD, HelpSayProvider, make_engine, request

FIXED = "Tell about irritation without a conflict"


class IntentProvider(HelpSayProvider):
    def __init__(self, intents: list[str]) -> None:
        super().__init__([GOOD])
        self.intents = intents

    async def generate(self, req: StructuredGenerationRequest) -> StructuredGenerationResponse:
        response = await super().generate(req)
        response.payload["result"]["preserved_intent"] = self.intents[min(len(self.requests), len(self.intents)) - 1]
        return response


def test_schema_requires_non_empty_preserved_intent() -> None:
    assert HelpSayResult.model_json_schema()["properties"]["preserved_intent"]["minLength"] == 1


@pytest.mark.asyncio
async def test_empty_preserved_intent_is_never_accepted_silently() -> None:
    with pytest.raises(RuntimeError, match="invalid result"):
        await make_engine(IntentProvider([""])).execute(WorkflowName.HELP_SAY, request())


@pytest.mark.asyncio
async def test_blank_preserved_intent_retries_with_specific_feedback_and_is_fixed() -> None:
    provider = IntentProvider(["   ", FIXED])
    result = await make_engine(provider).execute(WorkflowName.HELP_SAY, request())

    assert len(provider.requests) == 2
    instructions = provider.requests[1].user_payload["retry_instructions"]
    assert any("preserved_intent" in item for item in instructions)
    assert result.status == "ok"
    assert result.structured_result["preserved_intent"] == FIXED
