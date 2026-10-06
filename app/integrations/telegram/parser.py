from __future__ import annotations

from dataclasses import dataclass

from app.artifacts import WorkflowName


@dataclass(frozen=True, slots=True)
class ParsedTelegramRequest:
    workflow: WorkflowName
    text: str


_ALIASES: tuple[tuple[tuple[str, ...], WorkflowName], ...] = (
    (("/soften", "soften:", "смягчить:", "смягчи:"), WorkflowName.SOFTEN),
    (("/decode", "decode:", "расшифровать:", "расшифруй:"), WorkflowName.DECODE),
    (("/say", "/help-say", "help-say:", "скажи:", "помоги сказать:"), WorkflowName.HELP_SAY),
)


def _parse(text: str, default_workflow: WorkflowName) -> ParsedTelegramRequest:
    stripped = text.strip()
    lowered = stripped.casefold()
    for aliases, workflow in _ALIASES:
        for alias in aliases:
            if lowered.startswith(alias.casefold()):
                body = stripped[len(alias):].strip()
                return ParsedTelegramRequest(workflow=workflow, text=body)
    return ParsedTelegramRequest(workflow=default_workflow, text=stripped)


SERVICE_COMMANDS: frozenset[str] = frozenset({"start", "revoke", "delete", "export"})


def parse_service_command(text: str) -> str | None:
    """Return the service command name (start/revoke/delete/export) or None.

    Checked before workflow commands. Accepts an optional @botname suffix and ignores arguments.
    """
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    head = stripped.split(maxsplit=1)[0][1:]
    command = head.split("@", 1)[0].casefold()
    return command if command in SERVICE_COMMANDS else None


def parse_inline_query(text: str, default_workflow: WorkflowName = WorkflowName.SOFTEN) -> ParsedTelegramRequest:
    return _parse(text, default_workflow)


def parse_message_command(text: str, default_workflow: WorkflowName = WorkflowName.SOFTEN) -> ParsedTelegramRequest:
    return _parse(text, default_workflow)
