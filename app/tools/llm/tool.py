from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import ValidationError

from app.artifacts import ARTIFACT_MODELS
from app.artifacts.internal import SelfCheck, envelope_for
from app.tools.base import BaseTool, ToolResult
from app.tools.llm.base import StructuredGenerationRequest, StructuredLLMProvider

SelfCheckMode = Literal["observe", "required"]


class LLMGenerateTool(BaseTool):
    """Exactly one structured provider call per execute().

    With ``self_check=True`` the provider is always asked for the strict
    schema (result + self_check). An invalid result is a hard contract failure.
    A valid result is always returned with ``self_check_status`` (present |
    missing | malformed); ValidationStage applies ``self_check_mode``.
    """

    name = "llm_generate"
    capability = "structured_generation"

    def __init__(self, provider: StructuredLLMProvider, self_check_mode: SelfCheckMode = "required") -> None:
        self.provider = provider
        self.self_check_mode = self_check_mode

    async def execute(
        self,
        *,
        system_instructions: str,
        user_payload: dict[str, Any],
        output_artifact: str,
        metadata: dict[str, Any] | None = None,
        self_check: bool = False,
    ) -> ToolResult:
        try:
            result_model = ARTIFACT_MODELS[output_artifact]
            output_model = envelope_for(result_model) if self_check else result_model
            started = time.monotonic()
            response = await self.provider.generate(
                StructuredGenerationRequest(
                    system_instructions=system_instructions,
                    user_payload=user_payload,
                    output_model=output_model,
                    metadata=metadata or {},
                )
            )
            tool_metadata: dict[str, Any] = {
                "provider": response.provider,
                "model": response.model,
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
                "cost_usd": response.cost_usd,
                "provider_latency_ms": int((time.monotonic() - started) * 1000),
            }
            if not self_check:
                validated = result_model.model_validate(response.payload)
                return ToolResult(success=True, data=validated, metadata=tool_metadata)

            payload = response.payload
            try:
                validated = result_model.model_validate(payload.get("result"))
            except ValidationError:
                raise RuntimeError("generation contract failure: invalid result") from None

            check: SelfCheck | None = None
            if payload.get("self_check") is None:
                status = "missing"
            else:
                try:
                    check = SelfCheck.model_validate(payload["self_check"])
                    status = "present"
                except ValidationError:
                    status = "malformed"
            tool_metadata["self_check"] = check
            tool_metadata["self_check_status"] = status
            return ToolResult(success=True, data=validated, metadata=tool_metadata)
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))
