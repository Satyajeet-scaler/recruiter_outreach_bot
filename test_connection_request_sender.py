#!/usr/bin/env python3
"""Run connection request sender for one LinkedIn profile URL."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from services.linkedin_recruiter.connection_request_sender import send_connection_request_sync


def main() -> None:
    parser = argparse.ArgumentParser(description="Send LinkedIn connection request with note")
    parser.add_argument("--url", required=True, help="LinkedIn profile URL")
    parser.add_argument(
        "--storage",
        default="/home/satyajeet/Desktop/jobs_scraper/job_scaper/data/linkedin_storage.json",
        help="Path to Playwright linkedin_storage.json",
    )
    parser.add_argument("--headless", action="store_true", help="Run browser headless")
    parser.add_argument(
        "--no-fail-on-missing",
        action="store_true",
        help="Do not exit non-zero when connect/add-note/send is missing.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    result = send_connection_request_sync(
        profile_url=args.url,
        storage_state_path=args.storage,
        headless=args.headless,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))

    if args.no_fail_on_missing:
        return

    connect_clicked = bool(result.get("connect_clicked"))
    add_note_clicked = bool(result.get("add_note_clicked"))
    send_clicked = bool(result.get("send_clicked"))

    if not connect_clicked:
        logging.error("connect step not found/clicked; exiting with code 1")
        sys.exit(1)
    if not add_note_clicked:
        logging.error("add-note step not found/clicked; exiting with code 2")
        sys.exit(2)
    if not send_clicked:
        logging.error("send step not found/clicked; exiting with code 3")
        sys.exit(3)


if __name__ == "__main__":
    main()
