#!/usr/bin/env python3
"""Local CLI: Google Sheet → Gemini → LinkedIn outreach (same as POST /internal/run-sheet-pipeline).

Environment
-----------
  GOOGLE_SHEET_ID (or SPREADSHEET_ID)
  GOOGLE_SERVICE_ACCOUNT_JSON   Raw JSON string, or
  GOOGLE_APPLICATION_CREDENTIALS   Path to service account file
  GEMINI_API_KEY
  Optional: GEMINI_MODEL, LINKEDIN_STORAGE_PATH / default data path

Examples
--------
  python run_sheet_pipeline.py
  python run_sheet_pipeline.py --date 2026-04-17
  python run_sheet_pipeline.py --debug
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
        description="Run full sheet pipeline then LinkedIn batch outreach.",
    )
    p.add_argument(
        "--date",
        dest="run_date",
        type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
        default=None,
        help="Tab date YYYY-MM-DD (default: today).",
    )
    p.add_argument(
        "--storage-state-path",
        default=None,
        help="LinkedIn Playwright storage JSON (default: env or data/linkedin_storage.json).",
    )
    p.add_argument(
        "--debug",
        action="store_true",
        help="Pass debug=True into outreach (screenshots / verbose).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Only read sheet + Gemini; do not open browser or send outreach.",
    )
    return p.parse_args()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    args = _parse_args()
    run_date = args.run_date if args.run_date is not None else date.today()

    spreadsheet_id = os.getenv("GOOGLE_SHEET_ID") or os.getenv("SPREADSHEET_ID")
    if not spreadsheet_id:
        print("Set GOOGLE_SHEET_ID (or SPREADSHEET_ID)", file=sys.stderr)
        return 1

    from services.sheet_outreach.generate import generate_outreach_items, get_sheets_credentials
    from services.linkedin_recruiter import run_outreach_batch_sync

    try:
        creds = get_sheets_credentials()
    except ValueError as exc:
        print(f"Google credentials: {exc}", file=sys.stderr)
        return 1

    gen = generate_outreach_items(
        spreadsheet_id=spreadsheet_id,
        credentials=creds,
        run_date=run_date,
        dry_run=args.dry_run,
    )

    for w in gen.warnings:
        logging.warning("%s", w)
    for e in gen.errors:
        logging.error("%s", e)

    if not gen.items:
        print(
            json.dumps(
                {
                    "ok": True,
                    "run_date": gen.run_date.isoformat(),
                    "generated_count": 0,
                    "message": "No outreach items produced; skipped LinkedIn run.",
                },
                indent=2,
            )
        )
        return 0 if not gen.errors else 2

    if args.dry_run:
        print(json.dumps([dict(x) for x in gen.items], indent=2, ensure_ascii=False))
        return 0

    kwargs: dict = {"debug": args.debug}
    if args.storage_state_path:
        kwargs["storage_state_path"] = args.storage_state_path
    elif not os.getenv("LINKEDIN_STORAGE_PATH"):
        local_default = _ROOT / "data" / "linkedin_storage.json"
        kwargs["storage_state_path"] = str(local_default)

    results = run_outreach_batch_sync(gen.items, **kwargs)
    print(json.dumps(results, indent=2, ensure_ascii=False))
    successes = sum(1 for r in results if r.get("success"))
    total = len(results)
    print(f"\n--- {successes}/{total} outreach succeeded ---", file=sys.stderr)

    if successes < total or gen.errors:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
