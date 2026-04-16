#!/usr/bin/env python3
"""CLI script for batch LinkedIn outreach.

Usage
-----
  python run_outreach.py items.json
  python run_outreach.py items.json --storage-state-path /path/to/longin_storage.json

items.json format
-----------------
[
    {"profile_url": "https://www.linkedin.com/in/alice/", "message_text": "Hi Alice..."},
    {"profile_url": "https://www.linkedin.com/in/bob/",   "message_text": "Hi Bob..."}
]

For 1st-degree connections ``message_text`` is sent as a direct message.
For 2nd/3rd-degree connections it becomes the connection-request note
(truncated to 200 chars).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch LinkedIn outreach: message 1st-degree, connect 2nd/3rd-degree.",
    )
    parser.add_argument(
        "items_json",
        help='Path to JSON file with list of {"profile_url", "message_text"} dicts.',
    )
    parser.add_argument(
        "--storage-state-path",
        default=None,
        help="Path to LinkedIn storage_state JSON (uses default if omitted).",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug diagnostics (screenshots/verbose details). Disabled by default.",
    )
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = _parse_args()

    items_path = args.items_json
    try:
        with open(items_path, encoding="utf-8") as fh:
            items = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Failed to read {items_path}: {exc}", file=sys.stderr)
        return 1

    if not isinstance(items, list) or not items:
        print(f"Expected non-empty JSON list in {items_path}", file=sys.stderr)
        return 1

    try:
        from services.linkedin_recruiter import run_outreach_batch_sync
    except ModuleNotFoundError as exc:
        print(f"Missing dependency: {exc.name}\nInstall with: pip install {exc.name}", file=sys.stderr)
        return 2

    kwargs: dict = {"debug": args.debug}
    if args.storage_state_path:
        kwargs["storage_state_path"] = args.storage_state_path
    elif not os.getenv("LINKEDIN_STORAGE_PATH"):
        # Local default: fixed path in this repo.
        # In production, LINKEDIN_STORAGE_PATH should point to Railway volume.
        local_default = Path(__file__).resolve().parent / "data" / "linkedin_storage.json"
        kwargs["storage_state_path"] = str(local_default)

    results = run_outreach_batch_sync(items, **kwargs)

    print(json.dumps(results, indent=2, ensure_ascii=False))
    successes = sum(1 for r in results if r.get("success"))
    total = len(results)
    print(f"\n--- {successes}/{total} succeeded ---", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
