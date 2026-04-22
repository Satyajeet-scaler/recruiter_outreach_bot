#!/usr/bin/env python3
"""Sanity checks for shared context_builder merge behavior."""

from __future__ import annotations

import json

from services.context_builder import ContextEvent, build_context, parse_context_array


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    parsed = parse_context_array("")
    _assert(parsed == [], "Empty context should parse to []")

    parsed = parse_context_array("not-json")
    _assert(parsed == [], "Invalid context should parse to []")

    first_event = ContextEvent(
        event_type="generated_message",
        intent="onboard_recruiter_job",
        summary="Generated outreach note.",
        source="test_context_builder",
        payload={"profile_url": "https://www.linkedin.com/in/foo"},
        event_ts="2026-04-20T18:00:00Z",
        event_id="evt_1",
    )
    ctx1 = build_context(
        current_intent="onboard_recruiter_job",
        previous_context="[]",
        current_context=first_event,
    )
    items1 = json.loads(ctx1)
    _assert(len(items1) == 1, "First context append should add one event")

    # Dedupe by same event id/type/timestamp.
    ctx2 = build_context(
        current_intent="onboard_recruiter_job",
        previous_context=ctx1,
        current_context=first_event,
    )
    items2 = json.loads(ctx2)
    _assert(len(items2) == 1, "Duplicate event should not be appended")

    second_event = ContextEvent(
        event_type="message_sent",
        intent="onboard_recruiter_job",
        summary="Sent direct message.",
        source="test_context_builder",
        payload={"profile_url": "https://www.linkedin.com/in/foo"},
        event_ts="2026-04-20T18:05:00Z",
        event_id="evt_2",
    )
    ctx3 = build_context(
        current_intent="onboard_recruiter_job",
        previous_context=ctx2,
        current_context=second_event,
    )
    items3 = json.loads(ctx3)
    _assert(len(items3) == 2, "New event should append to context array")

    print("context_builder sanity checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
