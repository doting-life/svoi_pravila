from __future__ import annotations

from typing import Any

import httpx

from app.tools.llm.base import (
    StructuredGenerationRequest,
    StructuredGenerationResponse,
    StructuredLLMProvider,
)
from app.tools.llm.errors import LLMRateLimitError, LLMTimeoutError, LLMUnavailableError, safe_retry_after
from app.tools.llm.schema import output_json_schema, parse_structured_json, schema_name, user_input

DEFAULT_SBER500_BASE_URL = "https://shared1.multitool.works:4000/v1"


class Sber500StructuredLLMProvider(StructuredLLMProvider):
    """Sber500 / Disrupt OpenAI-compatible gateway (``POST /chat/completions``).

    Uses ``response_format={"type": "json_schema", "json_schema": {name, strict, schema}}``,
    verified against the gateway. One HTTP request per ``generate()``; no hidden retries.
    The API key, prompt text and response bodies are never logged or put into errors.
    """

    provider_name = "sber500"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = DEFAULT_SBER500_BASE_URL,
        timeout_seconds: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("SBER500_API_KEY is required when LLM_PROVIDER=sber500")
        if not model:
            raise ValueError("SBER500_MODEL is required when LLM_PROVIDER=sber500")
        self.model = model
        self._api_key = api_key
        self._chat_url = f"{base_url.rstrip('/')}/chat/completions"
        if client is not None:
            self.client = client
            self._owns_client = False
        else:
            self.client = httpx.AsyncClient(timeout=timeout_seconds)
            self._owns_client = True

    def __repr__(self) -> str:
        return f"Sber500StructuredLLMProvider(model={self.model!r})"

    async def generate(self, request: StructuredGenerationRequest) -> StructuredGenerationResponse:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": request.system_instructions},
                {"role": "user", "content": user_input(request.user_payload)},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name(request.output_model),
                    "strict": True,
                    "schema": output_json_schema(request.output_model),
                },
            },
            "stream": False,
        }
        try:
            response = await self.client.post(
                self._chat_url,
                headers={"Authorization": f"Bearer {self._api_key}", "Accept": "application/json"},
                json=body,
            )
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(f"Sber500 request timed out: {type(exc).__name__}", provider=self.provider_name) from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Sber500 request failed: {type(exc).__name__}") from exc

        if response.status_code == 429:
            raise LLMRateLimitError(
                "Sber500 rate limit (HTTP 429)",
                provider=self.provider_name,
                retry_after=safe_retry_after(response.headers.get("Retry-After")),
            )
        if response.status_code >= 500:
            raise LLMUnavailableError(f"Sber500 unavailable (HTTP {response.status_code})", provider=self.provider_name)
        if response.status_code != 200:
            raise RuntimeError(f"Sber500 chat completion failed with HTTP {response.status_code}")

        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("Sber500 returned an unexpected response shape") from exc

        payload = parse_structured_json(content, request.output_model, "Sber500")
        usage: dict[str, Any] = data.get("usage") or {}
        return StructuredGenerationResponse(
            payload=payload,
            provider=self.provider_name,
            model=str(data.get("model") or self.model),
            input_tokens=int(usage.get("prompt_tokens", 0) or 0),
            output_tokens=int(usage.get("completion_tokens", 0) or 0),
            cost_usd=None,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()
