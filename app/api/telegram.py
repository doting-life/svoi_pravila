from __future__ import annotations

import hashlib
import hmac
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status

from app.artifacts import AssistRequest, WorkflowName
from app.consent_texts import CONSENT_TEXT
from app.integrations.telegram import (
    TelegramWebAppUser,
    parse_inline_query,
    parse_message_command,
    parse_service_command,
)


router = APIRouter(prefix="/v1/telegram", tags=["telegram"])


def _workflow_title(workflow: WorkflowName) -> str:
    return {
        WorkflowName.SOFTEN: "Смягчить",
        WorkflowName.DECODE: "Расшифровать",
        WorkflowName.HELP_SAY: "Помоги сказать",
    }[workflow]


def _telegram_text(text: str) -> str:
    return text if len(text) <= 4096 else text[:4093] + "..."


def _inline_result(*, query_id: str, workflow: WorkflowName, text: str) -> dict[str, Any]:
    text = _telegram_text(text)
    stable_id = hashlib.sha256(f"{query_id}:{workflow.value}".encode("utf-8")).hexdigest()[:32]
    return {
        "type": "article",
        "id": stable_id,
        "title": _workflow_title(workflow),
        "description": text[:120],
        "input_message_content": {"message_text": text},
    }


CONSENT_REQUIRED_TEXT = "Чтобы пользоваться помощником, сначала дайте согласие на обработку персональных данных: откройте бота и отправьте /start."
CONSENT_ACCEPTED_TEXT = "Спасибо! Согласие получено. Напишите /soften, /decode или /say и затем текст."
CONSENT_DECLINED_TEXT = "Без согласия помощник не обрабатывает запросы. Если передумаете, отправьте /start."
ALREADY_ACTIVE_TEXT = "Согласие уже дано. Напишите /soften, /decode или /say и затем текст. Отозвать согласие: /revoke."
REVOKED_TEXT = "Согласие отозвано. Обработка запросов прекращена. Сохранённые данные можно выгрузить (/export) или удалить (/delete)."
NOT_ACTIVE_REVOKE_TEXT = "Активного согласия нет."
DELETE_PROMPT_TEXT = "Удалить аккаунт и все связанные данные? Это действие необратимо. Подтверждение действует 5 минут."
DELETE_DONE_TEXT = "Аккаунт и связанные данные удалены."
DELETE_FAILED_TEXT = "Не удалось удалить данные. Согласие отозвано, обработка остановлена. Повторите /delete позже."
EXPORT_PROMPT_TEXT = "Выгрузить сохранённые данные в файл? Подтверждение действует 5 минут."
EXPORT_CAPTION_TEXT = "Ваши данные в сервисе «Свои правила»."
EXPIRED_TEXT = "Запрос устарел. Повторите команду."
CANCELLED_TEXT = "Отменено."

CB_CONSENT_ACCEPT = "consent:accept"
CB_CONSENT_DECLINE = "consent:decline"
CB_DELETE_CONFIRM = "delete:confirm"
CB_DELETE_CANCEL = "delete:cancel"
CB_EXPORT_CONFIRM = "export:confirm"
CB_EXPORT_CANCEL = "export:cancel"


def _keyboard(*buttons: tuple[str, str]) -> dict[str, Any]:
    return {"inline_keyboard": [[{"text": text, "callback_data": data} for text, data in buttons]]}


async def _handle_service_command(container: Any, account: Any, command: str, sender: dict[str, Any], chat_id: Any) -> None:
    telegram = container.telegram
    telegram_user_id = int(sender["id"])
    if command == "start":
        if await account.is_active(telegram_user_id):
            await telegram.send_message(chat_id=chat_id, text=ALREADY_ACTIVE_TEXT)
            return
        await account.start(telegram_user_id)
        await telegram.send_message(
            chat_id=chat_id,
            text=CONSENT_TEXT,
            reply_markup=_keyboard(("Согласен", CB_CONSENT_ACCEPT), ("Не согласен", CB_CONSENT_DECLINE)),
        )
    elif command == "revoke":
        revoked = await account.revoke(telegram_user_id)
        await telegram.send_message(chat_id=chat_id, text=REVOKED_TEXT if revoked else NOT_ACTIVE_REVOKE_TEXT)
    elif command == "delete":
        await account.request_delete(telegram_user_id)
        await telegram.send_message(
            chat_id=chat_id,
            text=DELETE_PROMPT_TEXT,
            reply_markup=_keyboard(("Удалить", CB_DELETE_CONFIRM), ("Отмена", CB_DELETE_CANCEL)),
        )
    elif command == "export":
        await account.request_export(telegram_user_id)
        await telegram.send_message(
            chat_id=chat_id,
            text=EXPORT_PROMPT_TEXT,
            reply_markup=_keyboard(("Выгрузить", CB_EXPORT_CONFIRM), ("Отмена", CB_EXPORT_CANCEL)),
        )


async def _handle_callback(container: Any, account: Any, callback: dict[str, Any]) -> None:
    telegram = container.telegram
    callback_id = str(callback.get("id", ""))
    sender = callback.get("from") or {}
    data = callback.get("data")
    chat_id = ((callback.get("message") or {}).get("chat") or {}).get("id") or sender.get("id")
    await telegram.answer_callback_query(callback_query_id=callback_id)
    if sender.get("id") is None or chat_id is None:
        return
    telegram_user_id = int(sender["id"])
    if data == CB_CONSENT_ACCEPT:
        await account.accept(TelegramWebAppUser.model_validate(sender))
        await telegram.send_message(chat_id=chat_id, text=CONSENT_ACCEPTED_TEXT)
    elif data == CB_CONSENT_DECLINE:
        await account.decline(telegram_user_id)
        await telegram.send_message(chat_id=chat_id, text=CONSENT_DECLINED_TEXT)
    elif data == CB_DELETE_CONFIRM:
        result = await account.confirm_delete(telegram_user_id)
        reply = {"deleted": DELETE_DONE_TEXT, "failed": DELETE_FAILED_TEXT}.get(result.status, EXPIRED_TEXT)
        await telegram.send_message(chat_id=chat_id, text=reply)
    elif data == CB_EXPORT_CONFIRM:
        exported = await account.confirm_export(telegram_user_id)
        if exported.status != "ok":
            await telegram.send_message(chat_id=chat_id, text=EXPIRED_TEXT)
            return
        await telegram.send_document(
            chat_id=chat_id,
            filename=exported.filename,
            content=exported.content,
            caption=EXPORT_CAPTION_TEXT,
        )
    elif data in (CB_DELETE_CANCEL, CB_EXPORT_CANCEL):
        await account.cancel_pending(telegram_user_id)
        await telegram.send_message(chat_id=chat_id, text=CANCELLED_TEXT)


async def _consent_active(account: Any, sender: dict[str, Any]) -> bool:
    sender_id = sender.get("id")
    if sender_id is None:
        return False
    return await account.is_active(int(sender_id))


async def _resolve_internal_user(container: Any, sender: dict[str, Any]):
    telegram_user = TelegramWebAppUser.model_validate(sender)
    return await container.users.get_or_create_from_telegram(telegram_user)


async def _handle_update(container: Any, settings: Any, update: dict[str, Any]) -> None:
    telegram = container.telegram
    if telegram is None:
        return
    account = getattr(container, "account", None)
    if account is None:
        raise RuntimeError("Telegram is enabled but AccountService is not configured; refusing to process updates")

    callback = update.get("callback_query")
    if isinstance(callback, dict):
        await _handle_callback(container, account, callback)
        return

    inline = update.get("inline_query")
    if isinstance(inline, dict):
        query_id = str(inline.get("id", ""))
        query_text = str(inline.get("query", ""))
        sender = inline.get("from") or {}
        default = WorkflowName(settings.telegram_default_workflow)
        parsed = parse_inline_query(query_text, default)

        if not parsed.text:
            hints = [
                _inline_result(query_id=query_id, workflow=WorkflowName.SOFTEN, text="soften: текст сообщения"),
                _inline_result(query_id=query_id, workflow=WorkflowName.DECODE, text="decode: сообщение собеседника"),
                _inline_result(query_id=query_id, workflow=WorkflowName.HELP_SAY, text="скажи: что вы хотите донести"),
            ]
            await telegram.answer_inline_query(inline_query_id=query_id, results=hints)
            return

        if not await _consent_active(account, sender):
            await telegram.answer_inline_query(
                inline_query_id=query_id,
                results=[_inline_result(query_id=query_id, workflow=parsed.workflow, text=CONSENT_REQUIRED_TEXT)],
            )
            return

        try:
            user = await _resolve_internal_user(container, sender)
            delivery = await container.engine.execute(
                parsed.workflow,
                AssistRequest(
                    user_id=user.user_id,
                    text=parsed.text,
                    language=user.language_code or "ru",
                ),
            )
            results = [_inline_result(query_id=query_id, workflow=parsed.workflow, text=delivery.text)]
        except Exception:
            results = [_inline_result(
                query_id=query_id,
                workflow=parsed.workflow,
                text="Не удалось обработать запрос. Попробуйте ещё раз.",
            )]
        await telegram.answer_inline_query(inline_query_id=query_id, results=results)
        return

    message = update.get("message")
    if isinstance(message, dict) and isinstance(message.get("text"), str):
        sender = message.get("from") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None or sender.get("id") is None:
            return
        command = parse_service_command(message["text"])
        if command is not None:
            await _handle_service_command(container, account, command, sender, chat_id)
            return
        if not await _consent_active(account, sender):
            await telegram.send_message(chat_id=chat_id, text=CONSENT_REQUIRED_TEXT)
            return
        default = WorkflowName(settings.telegram_default_workflow)
        parsed = parse_message_command(message["text"], default)
        if not parsed.text:
            await telegram.send_message(
                chat_id=chat_id,
                text="Напишите /soften, /decode или /say и затем текст.",
            )
            return
        try:
            user = await _resolve_internal_user(container, sender)
            delivery = await container.engine.execute(
                parsed.workflow,
                AssistRequest(
                    user_id=user.user_id,
                    text=parsed.text,
                    language=user.language_code or "ru",
                ),
            )
            text = delivery.text
        except Exception:
            text = "Не удалось обработать запрос. Попробуйте ещё раз."
        await telegram.send_message(chat_id=chat_id, text=text)


@router.post("/webhook")
async def telegram_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, bool]:
    container = request.app.state.container
    settings = request.app.state.settings
    if not settings.telegram_enabled or container.telegram is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Telegram adapter is disabled")

    expected = settings.telegram_webhook_secret.get_secret_value() if settings.telegram_webhook_secret else None
    if expected:
        received = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not hmac.compare_digest(received, expected):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Telegram webhook secret")

    update = await request.json()
    background_tasks.add_task(_handle_update, container, settings, update)
    return {"ok": True}
