from __future__ import annotations

import asyncio

from app.integrations.telegram import TelegramBotClient
from app.settings import AppSettings


async def main() -> None:
    settings = AppSettings()
    if not settings.telegram_enabled or not settings.telegram_bot_token or not settings.telegram_webhook_url:
        raise SystemExit("TELEGRAM_ENABLED=true, TELEGRAM_BOT_TOKEN and TELEGRAM_WEBHOOK_URL are required")

    client = TelegramBotClient(settings.telegram_bot_token.get_secret_value())
    try:
        secret = settings.telegram_webhook_secret.get_secret_value() if settings.telegram_webhook_secret else None
        await client.set_webhook(
            url=settings.telegram_webhook_url,
            secret_token=secret,
            allowed_updates=["inline_query", "message", "callback_query"],
        )
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
