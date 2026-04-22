"""Context merge utilities used by all flows."""

from __future__ import annotations

import json
from typing import Any

from services.context_builder.types import ContextEvent


def parse_context_array(raw: str | None) -> list[dict[str, Any]]:
    """Parse context JSON array safely."""
    if not raw:
        return []
    text = str(raw).strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    out: list[dict[str, Any]] = []
    for item in parsed:
        if isinstance(item, dict):
            out.append(item)
    return out


def _dedupe_key(event: dict[str, Any]) -> tuple[str, str, str]:
    event_id = str(event.get("event_id") or "")
    event_type = str(event.get("event_type") or "")
    event_ts = str(event.get("event_ts") or "")
    return event_id, event_type, event_ts


def build_context(
    *,
    current_intent: str,
    previous_context: str | None,
    current_context: ContextEvent | dict[str, Any],
) -> str:
    """Merge previous context and current event into a JSON-array string."""
    items = parse_context_array(previous_context)
    if isinstance(current_context, ContextEvent):
        event = current_context.to_dict()
    else:
        event = dict(current_context)
        event.setdefault("intent", current_intent)
    event.setdefault("intent", current_intent)

    existing = {_dedupe_key(x) for x in items}
    key = _dedupe_key(event)
    if key not in existing:
        items.append(event)
    return json.dumps(items, ensure_ascii=True, separators=(",", ":"))
