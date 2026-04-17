#!/usr/bin/env python3
"""Test Sheet + Gemini only (no LinkedIn). Prints each row as LinkedIn ``profile_url`` + ``message_text``.

Output formats
--------------
  json (default): JSON array of objects, keys exactly ``profile_url`` and ``message_text``.
  text: Human-readable blocks (same fields, labeled).

Environment: same as ``generate_outreach_items_from_sheet.py`` (GOOGLE_SHEET_ID, etc.).

Examples
--------
  python test_sheet_before_outreach.py
  python test_sheet_before_outreach.py --format text
  python test_sheet_before_outreach.py -o items.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Output LinkedIn profile_url and message_text from Sheets + Gemini (no outreach).",
    )
    p.add_argument(
        "--date",
        dest="run_date",
        type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
        default=None,
        help="Run date YYYY-MM-DD (tabs must end with this date). Default: today.",
    )
    p.add_argument(
        "--spreadsheet-id",
        default=os.getenv("GOOGLE_SHEET_ID") or os.getenv("SPREADSHEET_ID"),
        help="Google Spreadsheet ID (or GOOGLE_SHEET_ID / SPREADSHEET_ID).",
    )
    p.add_argument(
        "--credentials",
        default=os.getenv("GOOGLE_APPLICATION_CREDENTIALS"),
        help="Service account JSON file path; omit if GOOGLE_SERVICE_ACCOUNT_JSON is set.",
    )
    p.add_argument(
        "-o",
        "--out",
        default=None,
        help="Write output to this file; default prints to stdout.",
    )
    p.add_argument(
        "--format",
        choices=("json", "text"),
        default="json",
        help="json: [{\"profile_url\": \"...\", \"message_text\": \"...\"}, ...]. text: labeled blocks.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Read sheets only; placeholder messages, no Gemini calls.",
    )
    p.add_argument(
        "--max-chars",
        type=int,
        default=int(os.getenv("OUTREACH_MESSAGE_MAX_CHARS", "300")),
        help="Max characters for each generated message (default 300).",
    )
    p.add_argument(
        "--model",
        default=os.getenv("GEMINI_MODEL"),
        help="Gemini model id (default env GEMINI_MODEL or gemini-2.5-flash).",
    )
    return p.parse_args()


def _format_text(rows: list[dict[str, str]]) -> str:
    parts: list[str] = []
    for i, row in enumerate(rows, start=1):
        parts.append(f"--- [{i}] ---")
        parts.append(f"profile_url: {row['profile_url']}")
        parts.append(f"message_text: {row['message_text']}")
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = _parse_args()
    run_date = args.run_date if args.run_date is not None else date.today()

    if not args.spreadsheet_id:
        print("Missing --spreadsheet-id or GOOGLE_SHEET_ID / SPREADSHEET_ID", file=sys.stderr)
        return 1

    from services.sheet_outreach.generate import (
        generate_outreach_items,
        get_sheets_credentials,
        items_for_outreach_json,
    )

    try:
        creds = get_sheets_credentials(credentials_path=args.credentials or None)
    except ValueError as exc:
        print(f"Google credentials: {exc}", file=sys.stderr)
        return 1

    result = generate_outreach_items(
        spreadsheet_id=args.spreadsheet_id,
        credentials=creds,
        run_date=run_date,
        model_name=args.model,
        max_message_chars=args.max_chars,
        dry_run=args.dry_run,
    )

    for w in result.warnings:
        logging.warning("%s", w)
    for e in result.errors:
        logging.error("%s", e)

    payload = items_for_outreach_json(result.items)

    if args.format == "json":
        out = json.dumps(payload, indent=2, ensure_ascii=False)
    else:
        out = _format_text(payload)

    if args.out:
        Path(args.out).write_text(out, encoding="utf-8")
        print(
            f"Wrote {len(payload)} rows (profile_url + message_text) to {args.out}",
            file=sys.stderr,
        )
    else:
        sys.stdout.write(out if out.endswith("\n") else out + "\n")

    if result.errors and not args.dry_run:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
