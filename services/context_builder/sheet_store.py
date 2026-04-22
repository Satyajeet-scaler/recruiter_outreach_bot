"""Google Sheets storage helpers for outreach context rows."""

from __future__ import annotations

import json
import logging
import os
from datetime import date
from typing import Any

from services.context_builder.builder import build_context
from services.context_builder.types import ContextEvent

logger = logging.getLogger(__name__)

OUTREACH_NOTES_TAB = "outreach_notes"
OUTREACH_NOTES_HEADERS = [
    "Date",
    "Recruiter Name",
    "Intent",
    "Job Url",
    "Recruiter Profile Url",
    "JD",
    "Context",
    "Personalized Note",
    "M. Received",
    "M. Replied",
    "Action Taken",
    "Success",
    "Skip Reason",
    "Source",
]
DEFAULT_MESSAGE_HISTORY = "[]"
DEFAULT_INTENT = "onboard_recruiter_job"


def _a1_column_label(index_1_based: int) -> str:
    if index_1_based < 1:
        raise ValueError("A1 column index must be >= 1")
    out: list[str] = []
    current = index_1_based
    while current > 0:
        current, rem = divmod(current - 1, 26)
        out.append(chr(ord("A") + rem))
    return "".join(reversed(out))


def get_sheets_credentials():
    from google.oauth2.service_account import Credentials

    scopes = ("https://www.googleapis.com/auth/spreadsheets",)
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if raw and raw.strip():
        info = json.loads(raw)
        return Credentials.from_service_account_info(info, scopes=scopes)
    path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if path:
        return Credentials.from_service_account_file(path, scopes=scopes)
    raise ValueError("Missing GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_APPLICATION_CREDENTIALS")


def open_spreadsheet(credentials: Credentials, spreadsheet_id: str):
    import gspread

    gc = gspread.authorize(credentials)
    return gc.open_by_key(spreadsheet_id)


def get_or_create_outreach_notes_worksheet(workbook: Any):
    try:
        return workbook.worksheet(OUTREACH_NOTES_TAB)
    except Exception:
        return workbook.add_worksheet(title=OUTREACH_NOTES_TAB, rows=2000, cols=32)


def ensure_outreach_notes_headers(worksheet: Any) -> None:
    current_header = worksheet.row_values(1)
    normalized_current = [str(x).strip() for x in current_header]
    if not any(normalized_current):
        end_col = _a1_column_label(len(OUTREACH_NOTES_HEADERS))
        worksheet.update(f"A1:{end_col}1", [OUTREACH_NOTES_HEADERS])
        return

    existing = {value: idx for idx, value in enumerate(normalized_current, start=1) if value}
    missing = [h for h in OUTREACH_NOTES_HEADERS if h not in existing]
    if not missing:
        return
    start_col = len(normalized_current) + 1
    end_col = start_col + len(missing) - 1
    worksheet.update(
        f"{_a1_column_label(start_col)}1:{_a1_column_label(end_col)}1",
        [missing],
    )


def _get_header_index_map(worksheet: Any) -> dict[str, int]:
    values = [str(x).strip() for x in worksheet.row_values(1)]
    out: dict[str, int] = {}
    for i, header in enumerate(values):
        if header:
            out[header] = i
    return out


def get_latest_context_for_profile(worksheet: Any, profile_url: str) -> str:
    profile = (profile_url or "").strip()
    if not profile:
        return "[]"
    rows = worksheet.get_all_values()
    if len(rows) <= 1:
        return "[]"
    idx_map = _get_header_index_map(worksheet)
    profile_idx = idx_map.get("Recruiter Profile Url")
    context_idx = idx_map.get("Context")
    if profile_idx is None or context_idx is None:
        return "[]"
    for row in reversed(rows[1:]):
        row_profile = row[profile_idx].strip() if profile_idx < len(row) else ""
        if row_profile == profile:
            return row[context_idx].strip() if context_idx < len(row) else "[]"
    return "[]"


def append_context_row(
    worksheet: Any,
    *,
    run_date: date,
    recruiter_name: str,
    intent: str,
    job_url: str,
    profile_url: str,
    jd: str,
    personalized_note: str,
    action_taken: str,
    success: bool | None,
    skip_reason: str,
    source: str,
    current_event: ContextEvent,
    message_received: str = DEFAULT_MESSAGE_HISTORY,
    message_replied: str = DEFAULT_MESSAGE_HISTORY,
) -> str:
    """Append a standardized outreach_notes row and return context JSON string."""
    ensure_outreach_notes_headers(worksheet)
    previous_context = get_latest_context_for_profile(worksheet, profile_url)
    context_json = build_context(
        current_intent=intent or DEFAULT_INTENT,
        previous_context=previous_context,
        current_context=current_event,
    )

    headers = _get_header_index_map(worksheet)
    row = [""] * len(headers)

    def put(key: str, value: Any) -> None:
        idx = headers.get(key)
        if idx is not None:
            row[idx] = "" if value is None else str(value)

    put("Date", run_date.isoformat())
    put("Recruiter Name", recruiter_name)
    put("Intent", intent or DEFAULT_INTENT)
    put("Job Url", job_url)
    put("Recruiter Profile Url", profile_url)
    put("JD", jd)
    put("Context", context_json)
    put("Personalized Note", personalized_note)
    put("M. Received", message_received or DEFAULT_MESSAGE_HISTORY)
    put("M. Replied", message_replied or DEFAULT_MESSAGE_HISTORY)
    put("Action Taken", action_taken)
    put("Success", "" if success is None else str(bool(success)).lower())
    put("Skip Reason", skip_reason)
    put("Source", source)
    worksheet.append_row(row, value_input_option="RAW")
    return context_json


def append_context_row_from_env(
    *,
    run_date: date,
    recruiter_name: str = "",
    intent: str = DEFAULT_INTENT,
    job_url: str = "",
    profile_url: str = "",
    jd: str = "",
    personalized_note: str = "",
    action_taken: str = "",
    success: bool | None = None,
    skip_reason: str = "",
    source: str = "",
    current_event: ContextEvent,
    message_received: str = DEFAULT_MESSAGE_HISTORY,
    message_replied: str = DEFAULT_MESSAGE_HISTORY,
) -> bool:
    """Best-effort append; returns False when not configured or append fails."""
    spreadsheet_id = os.getenv("GOOGLE_SHEET_ID") or os.getenv("SPREADSHEET_ID")
    if not spreadsheet_id:
        logger.info("Skipping context append; GOOGLE_SHEET_ID/SPREADSHEET_ID not set.")
        return False
    try:
        creds = get_sheets_credentials()
        workbook = open_spreadsheet(creds, spreadsheet_id)
        ws = get_or_create_outreach_notes_worksheet(workbook)
        append_context_row(
            ws,
            run_date=run_date,
            recruiter_name=recruiter_name,
            intent=intent,
            job_url=job_url,
            profile_url=profile_url,
            jd=jd,
            personalized_note=personalized_note,
            action_taken=action_taken,
            success=success,
            skip_reason=skip_reason,
            source=source,
            current_event=current_event,
            message_received=message_received,
            message_replied=message_replied,
        )
        return True
    except Exception as exc:
        logger.warning("Context append failed source=%s profile=%s err=%s", source, profile_url, exc)
        return False
