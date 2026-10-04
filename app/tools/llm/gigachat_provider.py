from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

import httpx

from app.tools.llm.base import (
    StructuredGenerationRequest,
    StructuredGenerationResponse,
    StructuredLLMProvider,
)
from app.tools.llm.errors import LLMRateLimitError, LLMTimeoutError, LLMUnavailableError, safe_retry_after
from app.tools.llm.schema import output_json_schema, parse_structured_json, user_input

DEFAULT_GIGACHAT_BASE_URL = "https://api.giga.chat/v1"
DEFAULT_GIGACHAT_AUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
DEFAULT_GIGACHAT_SCOPE = "GIGACHAT_API_PERS"

# Access tokens live 30 minutes; refresh this many seconds before expiry.
TOKEN_REFRESH_MARGIN_SECONDS = 60.0
TOKEN_FALLBACK_TTL_SECONDS = 30 * 60


class GigaChatStructuredLLMProvider(StructuredLLMProvider):
    """GigaChat REST v1 chat completions adapter with strict JSON Schema output.

    Request: ``response_format={"type": "json_schema", "schema": ..., "strict": true}``.
    Response: JSON string at ``choices[0].message.content``, validated with the
    requested Pydantic model. Credentials, tokens and prompt text are never logged
    or included in error messages.
    """

    def __init__(
        self,
        *,
        credentials: str,
        model: str,
        scope: str = DEFAULT_GIGACHAT_SCOPE,
        base_url: str = DEFAULT_GIGACHAT_BASE_URL,
        auth_url: str = DEFAULT_GIGACHAT_AUTH_URL,
        timeout_seconds: float = 30.0,
        verify: bool | str = True,
        client: httpx.AsyncClient | None = None,
        clock: Any = time.monotonic,
    ) -> None:
        if not credentials:
            raise ValueError("GIGACHAT_CREDENTIALS is required when LLM_PROVIDER=gigachat")
        self.model = model
        self._credentials = credentials
        self._scope = scope
        self._chat_url = f"{base_url.rstrip('/')}/chat/completions"
        self._auth_url = auth_url
        self._clock = clock
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = asyncio.Lock()
        if client is not None:
            self.client = client
            self._owns_client = False
        else:
            self.client = httpx.AsyncClient(timeout=timeout_seconds, verify=verify)
            self._owns_client = True

    def __repr__(self) -> str:
        return f"GigaChatStructuredLLMProvider(model={self.model!r})"

    async def _access_token(self) -> str:
        async with self._token_lock:
            if self._token and self._clock() < self._token_expires_at - TOKEN_REFRESH_MARGIN_SECONDS:
                return self._token
            try:
                response = await self.client.post(
                    self._auth_url,
                    headers={
                        "Authorization": f"Basic {self._credentials}",
                        "RqUID": str(uuid.uuid4()),
                        "Accept": "application/json",
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                    data={"scope": self._scope},
                )
            except httpx.HTTPError as exc:
                raise RuntimeError(f"GigaChat auth request failed: {type(exc).__name__}") from exc
            if response.status_code != 200:
                raise RuntimeError(f"GigaChat auth failed with HTTP {response.status_code}")
            try:
                body = response.json()
                token = body["access_token"]
            except (ValueError, KeyError, TypeError) as exc:
                raise RuntimeError("GigaChat auth returned no access token") from exc
            if not isinstance(token, str) or not token:
                raise RuntimeError("GigaChat auth returned no access token")
            self._token = token
            self._token_expires_at = self._clock() + _ttl_seconds(body.get("expires_at"))
            return token

    def _invalidate_token(self) -> None:
        self._token = None
        self._token_expires_at = 0.0

    async def generate(self, request: StructuredGenerationRequest) -> StructuredGenerationResponse:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": request.system_instructions},
                {"role": "user", "content": user_input(request.user_payload)},
            ],
            "response_format": {
                "type": "json_schema",
                "schema": output_json_schema(request.output_model),
                "strict": True,
            },
            "stream": False,
        }

        response = await self._post_chat(body)
        if response.status_code == 401:
            # Token revoked or expired early: refresh once and retry.
            self._invalidate_token()
            response = await self._post_chat(body)
        if response.status_code == 429:
            raise LLMRateLimitError(
                "GigaChat rate limit (HTTP 429)",
                provider="gigachat",
                retry_after=safe_retry_after(response.headers.get("Retry-After")),
            )
        if response.status_code >= 500:
            raise LLMUnavailableError(f"GigaChat unavailable (HTTP {response.status_code})", provider="gigachat")
        if response.status_code != 200:
            raise RuntimeError(f"GigaChat chat completion failed with HTTP {response.status_code}")

        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("GigaChat returned an unexpected response shape") from exc

        payload = parse_structured_json(content, request.output_model, "GigaChat")
        usage = data.get("usage") or {}
        return StructuredGenerationResponse(
            payload=payload,
            provider="gigachat",
            model=str(data.get("model") or self.model),
            input_tokens=int(usage.get("prompt_tokens", 0) or 0),
            output_tokens=int(usage.get("completion_tokens", 0) or 0),
            cost_usd=None,
        )

    async def _post_chat(self, body: dict[str, Any]) -> httpx.Response:
        token = await self._access_token()
        try:
            return await self.client.post(
                self._chat_url,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                json=body,
            )
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(f"GigaChat request timed out: {type(exc).__name__}", provider="gigachat") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(f"GigaChat request failed: {type(exc).__name__}") from exc

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()


def _ttl_seconds(expires_at: Any) -> float:
    """Convert GigaChat ``expires_at`` (Unix epoch, ms) into a TTL in seconds."""
    if isinstance(expires_at, (int, float)) and expires_at > 0:
        epoch_seconds = expires_at / 1000 if expires_at > 1e11 else float(expires_at)
        ttl = epoch_seconds - time.time()
        if ttl > 0:
            return min(ttl, float(TOKEN_FALLBACK_TTL_SECONDS))
    return float(TOKEN_FALLBACK_TTL_SECONDS)
