"""In-process, direct-source-only chat resolution.

The resolver deliberately accepts only rows already returned by the direct
``sessions`` and ``contacts`` tools.  It does not inspect a database, call the
upstream ``resolve-chat`` command, start another process, or persist a value.
The internal chat value is carried only by the returned Python object and is
never part of its public representation.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
import re
from typing import Any, Iterable


_INTERNAL_LABEL_PATTERN = re.compile(
    r"(?i)(?:^wxid_[a-z0-9_.-]+$|^gh_[a-z0-9_.-]+$|^[0-9]{7,}(?:@chatroom)?$|^.+@chatroom$)"
)


def _fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    return " ".join(normalized.split())


def _rows(payload: Any, names: tuple[str, ...]) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    containers: list[Any] = [payload]
    data = payload.get("data")
    if isinstance(data, dict):
        containers.append(data)
    for container in containers:
        for name in names:
            value = container.get(name) if isinstance(container, dict) else None
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _first_string(row: dict[str, Any], names: tuple[str, ...]) -> str | None:
    for name in names:
        value = row.get(name)
        if isinstance(value, str) and value.strip():
            return value
    return None


@dataclass(frozen=True)
class Candidate:
    label: str
    internal_chat: str


@dataclass(frozen=True)
class Resolution:
    """A resolution whose internal value is intentionally not serializable."""

    ok: bool
    match_count: int
    exact: bool
    partial: bool
    ambiguous: bool
    internal_chat: str | None = None

    def public(self, *, backend_used: str = "direct", tool_name: str = "resolve_chat") -> dict[str, Any]:
        """Return the only representation allowed to cross the MCP boundary."""

        return {
            "ok": self.ok,
            "tool": tool_name,
            "backend_used": backend_used,
            "match_count": self.match_count,
            "exact": self.exact,
            "partial": self.partial,
            "ambiguous": self.ambiguous,
            "redacted": True,
            "body_emitted": False,
            "identifier_emitted": False,
        }


def _candidate(label: Any, internal: Any) -> Candidate | None:
    if not isinstance(label, str) or not label.strip():
        return None
    if not isinstance(internal, str) or not internal.strip():
        return None
    # Fail closed when an upstream row repeats its backend identifier in a
    # display field. Such a value must never become a public resolver query.
    folded_label = _fold(label)
    folded_internal = _fold(internal)
    if folded_label == folded_internal or _INTERNAL_LABEL_PATTERN.fullmatch(label.strip()):
        return None
    return Candidate(label=label, internal_chat=internal)


def candidates_from_sources(sessions_payload: Any, contacts_payload: Any) -> list[Candidate]:
    """Build an in-memory candidate set from direct session/contact rows only."""

    candidates: list[Candidate] = []
    session_rows = _rows(sessions_payload, ("sessions", "rows"))
    contact_rows = _rows(contacts_payload, ("contacts", "rows"))

    # A direct row can expose several user-facing labels for the same chat.
    # Validate each label independently: a backend-shaped display_name must
    # not hide a later safe nickname/remark, and no rejected label may cross
    # the MCP boundary.
    label_fields = ("display_name", "nick_name", "nickname", "remark", "alias")
    for row in (*session_rows, *contact_rows):
        internal = _first_string(row, ("username", "talker", "chat"))
        for field in label_fields:
            item = _candidate(row.get(field), internal)
            if item is not None:
                candidates.append(item)

    seen: set[tuple[str, str]] = set()
    unique: list[Candidate] = []
    for item in candidates:
        key = (item.label, item.internal_chat)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def _distinct_internal(items: Iterable[Candidate]) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item.internal_chat not in seen:
            seen.add(item.internal_chat)
            values.append(item.internal_chat)
    return values


def resolve(candidates: Iterable[Candidate], query: str) -> Resolution:
    if not isinstance(query, str) or not query.strip():
        return Resolution(False, 0, False, False, False)

    materialized = list(candidates)
    folded_query = _fold(query)
    exact_items = [item for item in materialized if _fold(item.label) == folded_query]
    exact = bool(exact_items)
    selected = exact_items or [
        item
        for item in materialized
        if folded_query and folded_query in _fold(item.label)
    ]
    values = _distinct_internal(selected)

    if len(values) == 1:
        return Resolution(True, 1, exact, not exact, False, values[0])
    if len(values) > 1:
        return Resolution(True, len(values), exact, not exact, True, None)
    return Resolution(False, 0, False, False, False, None)


__all__ = ["Candidate", "Resolution", "candidates_from_sources", "resolve"]
