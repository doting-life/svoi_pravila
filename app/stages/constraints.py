from __future__ import annotations

import re

from app.artifacts import RelationshipRule

# Phrases quoted inside an "avoid" rule, e.g. 'ty vsegda' and 'ty nikogda'.
_QUOTED = re.compile(
    r"'([^']+)'"
    r'|"([^"]+)"'
    "|\u00ab([^\u00bb]+)\u00bb"
    "|\u201c([^\u201d]+)\u201d"
    "|\u201e([^\u201c]+)\u201c"
)
_WS = re.compile(r"\s+")


def _normalize(text: str) -> str:
    return _WS.sub(" ", text.replace("\u0451", "\u0435").replace("\u0401", "\u0415")).strip().casefold()


def avoided_phrases(rules: list[RelationshipRule]) -> list[str]:
    """Explicit phrases forbidden by ``avoid`` relationship rules (quoted text only)."""
    phrases: list[str] = []
    for rule in rules:
        if rule.type.strip().casefold() != "avoid":
            continue
        for match in _QUOTED.finditer(rule.value):
            phrase = _normalize(next(group for group in match.groups() if group))
            if phrase and phrase not in phrases:
                phrases.append(phrase)
    return phrases


def find_avoided_phrases(text: str, rules: list[RelationshipRule]) -> list[str]:
    """Avoided phrases present in ``text`` as whole words (case-insensitive, yo == ye)."""
    haystack = _normalize(text)
    return [
        phrase
        for phrase in avoided_phrases(rules)
        if re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", haystack)
    ]
