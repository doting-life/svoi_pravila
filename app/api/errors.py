"""Map typed LLM provider failures to HTTP statuses at the FastAPI boundary."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.tools.llm.errors import LLMProviderError, LLMRateLimitError, LLMTimeoutError, LLMUnavailableError

_MAPPING: tuple[tuple[type[LLMProviderError], int, str], ...] = (
    (LLMRateLimitError, 429, "The AI provider is busy. Please try again later."),
    (LLMTimeoutError, 504, "The AI provider did not respond in time."),
    (LLMUnavailableError, 503, "The AI provider is temporarily unavailable."),
)


async def provider_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, LLMProviderError)
    for error_type, status_code, detail in _MAPPING:
        if isinstance(exc, error_type):
            headers = {"Retry-After": exc.retry_after} if status_code == 429 and exc.retry_after else None
            return JSONResponse(
                status_code=status_code,
                content={"detail": detail, "error": f"provider_{exc.category}"},
                headers=headers,
            )
    return JSONResponse(status_code=502, content={"detail": "The AI provider failed.", "error": "provider_error"})


def register_provider_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(LLMProviderError, provider_error_handler)
