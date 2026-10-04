from __future__ import annotations

# FROZEN TEMPORARY REGRESSION GUARD (Russian variants only: ru, ru-RU, ...).
# The primary, language-agnostic mechanism is the model self-check code
# "invented_commitments" (app/stages/self_check.py). Do not add patterns or
# languages here. Removal: once real-provider/shadow evaluation shows the
# self-check catches the known regression corpus, switch to log-only, then delete.

import re

# High-confidence commitment/proposal patterns for the help-say workflow.
# Each pattern belongs to a family; a family is allowed when the user's own
# source text already contains a commitment of that family.
_SELF = "self"
_MUTUAL = "mutual"

# "я буду рад(а)" / "мы будем благодарны" are polite wishes, not commitments.
_NOT_COMMITMENT = r"(?!\s+(?:рад|рада|рады|благодар\w*|признател\w*|счастлив\w*))"

COMMITMENT_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = tuple(
    (label, family, re.compile(rf"(?<!\w){regex}(?!\w)"))
    for label, family, regex in (
        ("я обещаю", _SELF, r"я\s+обещаю"),
        ("я обязуюсь", _SELF, r"я\s+обязуюсь"),
        ("я буду", _SELF, r"я\s+буду" + _NOT_COMMITMENT),
        ("я постараюсь", _SELF, r"я\s+постараюсь"),
        ("мы будем", _MUTUAL, r"мы\s+будем" + _NOT_COMMITMENT),
        ("мы постараемся", _MUTUAL, r"мы\s+постараемся"),
        ("давай будем", _MUTUAL, r"давай(?:те)?\s+будем"),
        ("давай стараться", _MUTUAL, r"давай(?:те)?\s+стараться"),
        ("давай постараемся", _MUTUAL, r"давай(?:те)?\s+постараемся"),
        ("давай договоримся", _MUTUAL, r"давай(?:те)?\s+договоримся"),
    )
)

# Source cues showing the user already asked for a commitment of that family.
_SOURCE_CUES: dict[str, re.Pattern[str]] = {
    _SELF: re.compile(r"(?<!\w)(?:обеща\w*|обязу\w*|буду|постараюсь)(?!\w)"),
    _MUTUAL: re.compile(
        r"(?<!\w)(?:мы\s+будем|мы\s+постараемся|давай(?:те)?\s+(?:будем|стараться|постараемся|договоримся)"
        r"|договор\w*|друг\s+(?:друга|другу|с\s+другом)|вместе|взаимн\w*|оба|обе)(?!\w)"
    ),
}

_WS = re.compile(r"\s+")


def _normalize(text: str) -> str:
    return _WS.sub(" ", text.replace("ё", "е").replace("Ё", "Е")).strip().casefold()


def find_invented_commitments(message: str, source: str) -> list[str]:
    """Commitment patterns present in ``message`` that the user's ``source`` did not ask for."""
    generated = _normalize(message)
    original = _normalize(source)
    found: list[str] = []
    for label, family, pattern in COMMITMENT_PATTERNS:
        if not pattern.search(generated):
            continue
        if pattern.search(original) or _SOURCE_CUES[family].search(original):
            continue
        if label not in found:
            found.append(label)
    return found
