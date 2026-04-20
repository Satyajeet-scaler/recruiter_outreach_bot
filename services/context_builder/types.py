"""Shared types for outreach context building."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class ContextEvent:
    """Single context event item persisted in outreach notes context array."""

    event_type: str
    intent: str
    summary: str
    source: str
    payload: dict[str, Any] = field(default_factory=dict)
    event_ts: str = field(default_factory=utc_now_iso)
    event_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        out = {
            "event_type": self.event_type,
            "intent": self.intent,
            "summary": self.summary,
            "source": self.source,
            "payload": self.payload or {},
            "event_ts": self.event_ts,
        }
        if self.event_id:
            out["event_id"] = self.event_id
        return out
