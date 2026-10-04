from app.artifacts import DecodeResult, DeliveryResponse, HelpSayResult, SoftenResult, ValidationResult, WorkflowName
from app.config import StageManifest
from app.stages.base import BaseStage, StageContext
from app.workflows.state import WorkflowState


VALIDATION_FAILURE_TEXT = {
    "ru": "Не удалось получить корректный ответ.",
    "en": "Could not produce a valid response.",
    "he": "לא הצלחנו להפיק תשובה תקינה.",
}


def validation_failure_text(language: str | None) -> str:
    """Deterministic fallback text; variants (en-US, ru_RU) use the base language, unknown -> English."""
    base = (language or "").strip().lower().replace("_", "-").split("-")[0]
    if base == "iw":  # legacy Hebrew code
        base = "he"
    return VALIDATION_FAILURE_TEXT.get(base, VALIDATION_FAILURE_TEXT["en"])


class DeliveryStage(BaseStage):
    async def execute(self, state: WorkflowState, manifest: StageManifest, context: StageContext) -> DeliveryResponse:
        validation = state.artifacts["validation_result"]
        assert isinstance(validation, ValidationResult)
        if validation.status != "pass":
            return DeliveryResponse(
                request_id=state.request_id,
                workflow=WorkflowName(state.workflow),
                status="error",
                text=validation_failure_text(context.api_request.language),
            )

        if "soften_result" in state.artifacts:
            result = state.artifacts["soften_result"]
            assert isinstance(result, SoftenResult)
            text = result.rewritten_message
        elif "decode_result" in state.artifacts:
            result = state.artifacts["decode_result"]
            assert isinstance(result, DecodeResult)
            text = (
                f"Буквально: {result.literal_meaning}\n"
                f"Возможное намерение: {result.probable_intent}\n"
                f"Тон: {result.emotional_tone}\n"
                f"Неопределённость: {result.uncertainty}"
            )
        else:
            result = state.artifacts["help_say_result"]
            assert isinstance(result, HelpSayResult)
            text = result.message

        return DeliveryResponse(
            request_id=state.request_id,
            workflow=WorkflowName(state.workflow),
            status="ok",
            text=text,
            structured_result=result.model_dump(mode="json"),
        )
