"""Shared context builder service."""

from services.context_builder.builder import build_context, parse_context_array
from services.context_builder.types import ContextEvent

__all__ = [
    "ContextEvent",
    "build_context",
    "parse_context_array",
]

try:
    from services.context_builder.sheet_store import (
        DEFAULT_INTENT,
        OUTREACH_NOTES_HEADERS,
        OUTREACH_NOTES_TAB,
        append_context_row,
        append_context_row_from_env,
        ensure_outreach_notes_headers,
        get_or_create_outreach_notes_worksheet,
    )

    __all__.extend(
        [
            "DEFAULT_INTENT",
            "OUTREACH_NOTES_HEADERS",
            "OUTREACH_NOTES_TAB",
            "append_context_row",
            "append_context_row_from_env",
            "ensure_outreach_notes_headers",
            "get_or_create_outreach_notes_worksheet",
        ]
    )
except Exception:
    # Keep core builder imports available even when Sheets deps are missing.
    pass
