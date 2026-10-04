from __future__ import annotations

from typing import Any

from app.artifacts import DecodeResult, HelpSayResult, RelationshipContext, SoftenResult
from app.artifacts.internal import GenerationEnvelope
from app.stages.constraints import find_avoided_phrases
from app.tools.llm.base import (
    StructuredGenerationRequest,
    StructuredGenerationResponse,
    StructuredLLMProvider,
)


class FakeStructuredLLMProvider(StructuredLLMProvider):
    """Deterministic provider used for local development and tests.

    It intentionally does not try to be clever. Its purpose is to validate the
    orchestration, contracts, retries, and API surfaces without external calls.
    """

    async def generate(self, request: StructuredGenerationRequest) -> StructuredGenerationResponse:
        source = str(request.user_payload["message_request"]["text"])
        request_id = request.user_payload["message_request"]["request_id"]
        output_model = request.output_model
        envelope = isinstance(output_model, type) and issubclass(output_model, GenerationEnvelope)
        if envelope:
            output_model = output_model.model_fields["result"].annotation

        if output_model is SoftenResult:
            rewritten = self._soften(source)
            payload: dict[str, Any] = {
                "version": "1.0",
                "request_id": request_id,
                "original_intent": source,
                "rewritten_message": rewritten,
                "tone_applied": "calm_direct",
                "constraints_respected": self._respected_constraints(request.user_payload, source, rewritten),
            }
        elif output_model is DecodeResult:
            payload = {
                "version": "1.0",
                "request_id": request_id,
                "literal_meaning": source,
                "probable_intent": "Возможное намерение зависит от контекста; это лишь вероятная интерпретация.",
                "emotional_tone": "неопределённый",
                "uncertainty": "Высокая: без дополнительного контекста нельзя уверенно установить мотив автора.",
                "alternative_interpretations": [
                    "Буквальное сообщение без скрытого подтекста.",
                    "Попытка обозначить недовольство или ожидание ответа.",
                ],
            }
        elif output_model is HelpSayResult:
            payload = {
                "version": "1.0",
                "request_id": request_id,
                "message": source.strip(),
                "tone": "direct_natural",
                "preserved_intent": source,
                "warnings": [],
            }
        else:
            raise ValueError(f"Unsupported fake output model: {output_model.__name__}")

        if envelope:
            payload = {"result": payload, "self_check": {"all_passed": True, "failures": [], "violated_rule_ids": []}}

        return StructuredGenerationResponse(
            payload=payload,
            provider="fake",
            model="fake-structured-v1",
            input_tokens=max(len(source) // 4, 1),
            output_tokens=max(len(str(payload)) // 4, 1),
            cost_usd=0.0,
        )

    @staticmethod
    def _respected_constraints(user_payload: dict[str, Any], source: str, rewritten: str) -> list[str]:
        """Report only applicable plan constraints: avoid-rules whose phrase was present and was removed."""
        plan_constraints = (user_payload.get("generation_plan") or {}).get("constraints") or []
        context_payload = user_payload.get("relationship_context")
        if not context_payload:
            return []
        context_payload = {
            **context_payload,
            "rules": [
                {key: value for key, value in rule.items() if key != "rule_id"}
                for rule in context_payload.get("rules", [])
            ],
        }
        context = RelationshipContext.model_validate(context_payload)
        return [
            rule.value
            for rule in context.rules
            if rule.value in plan_constraints
            and find_avoided_phrases(source, [rule])
            and not find_avoided_phrases(rewritten, [rule])
        ]

    @staticmethod
    def _soften(text: str) -> str:
        substitutions = {
            "ты опять": "мне важно обратить внимание, что снова",
            "Ты опять": "Мне важно обратить внимание, что снова",
            "ничего не сделал": "это осталось несделанным",
            "ничего не сделала": "это осталось несделанным",
            "всегда": "часто",
            "никогда": "редко",
        }
        result = text
        for old, new in substitutions.items():
            result = result.replace(old, new)
        return result.strip()
