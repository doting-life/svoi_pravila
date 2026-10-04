"""Typed, privacy-safe LLM provider failures.

Messages carry only the provider name and a safe category/status; never
credentials, prompts, model output or provider response bodies.
"""
from __future__ import annotations

import re

_RETRY_AFTER_SECONDS = re.compile(r"^\d{1,6}$")


class LLMProviderError(RuntimeError):
    category = "provider_error"

    def __init__(self, message: str, *, provider: str, retry_after: str | None = None) -> None:
        super().__init__(message)
        self.provider = provider
        self.retry_after = retry_after


class LLMRateLimitError(LLMProviderError):
    category = "rate_limit"


class LLMTimeoutError(LLMProviderError):
    category = "timeout"


class LLMUnavailableError(LLMProviderError):
    category = "unavailable"


def safe_retry_after(value: str | None) -> str | None:
    """Keep Retry-After only in its delay-seconds form (digits)."""
    if value is None:
        return None
    value = value.strip()
    return value if _RETRY_AFTER_SECONDS.match(value) else None
