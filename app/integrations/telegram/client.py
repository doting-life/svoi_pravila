from __future__ import annotations

from typing import Any

import httpx


class TelegramBotClient:
    def __init__(self, bot_token: str, *, timeout_seconds: float = 15.0) -> None:
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self.client = httpx.AsyncClient(timeout=timeout_seconds)

    async def _post(self, method: str, payload: dict[str, Any]) -> Any:
        response = await self.client.post(f"{self.base_url}/{method}", json=payload)
        response.raise_for_status()
        body = response.json()
        if not body.get("ok"):
            raise RuntimeError(f"Telegram API {method} failed: {body.get('description', 'unknown error')}")
        return body.get("result")

    async def answer_inline_query(
        self,
        *,
        inline_query_id: str,
        results: list[dict[str, Any]],
    ) -> bool:
        result = await self._post(
            "answerInlineQuery",
            {
                "inline_query_id": inline_query_id,
                "results": results,
                "cache_time": 0,
                "is_personal": True,
            },
        )
        return bool(result)

    async def send_message(
        self,
        *,
        chat_id: int | str,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        result = await self._post("sendMessage", payload)
        return dict(result or {})

    async def answer_callback_query(self, *, callback_query_id: str, text: str | None = None) -> bool:
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        result = await self._post("answerCallbackQuery", payload)
        return bool(result)

    async def send_document(
        self,
        *,
        chat_id: int | str,
        filename: str,
        content: bytes,
        caption: str | None = None,
        mime_type: str = "application/json",
    ) -> dict[str, Any]:
        data: dict[str, Any] = {"chat_id": str(chat_id)}
        if caption:
            data["caption"] = caption
        response = await self.client.post(
            f"{self.base_url}/sendDocument",
            data=data,
            files={"document": (filename, content, mime_type)},
        )
        response.raise_for_status()
        body = response.json()
        if not body.get("ok"):
            raise RuntimeError(f"Telegram API sendDocument failed: {body.get('description', 'unknown error')}")
        return dict(body.get("result") or {})

    async def set_webhook(
        self,
        *,
        url: str,
        secret_token: str | None = None,
        allowed_updates: list[str] | None = None,
    ) -> bool:
        payload: dict[str, Any] = {
            "url": url,
            "allowed_updates": allowed_updates or ["inline_query", "message", "callback_query"],
        }
        if secret_token:
            payload["secret_token"] = secret_token
        result = await self._post("setWebhook", payload)
        return bool(result)

    async def aclose(self) -> None:
        await self.client.aclose()
