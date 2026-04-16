#!/usr/bin/env python3
"""Run LinkedIn message sender for one profile URL."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from services.linkedin_recruiter.message_sender import send_linkedin_message_sync


def main() -> None:
    parser = argparse.ArgumentParser(description="Send LinkedIn message from profile Message button")
    parser.add_argument("--url", required=True, help="LinkedIn profile URL")
    parser.add_argument(
        "--storage",
        default="/home/satyajeet/Desktop/jobs_scraper/job_scaper/data/linkedin_storage.json",
        help="Path to Playwright linkedin_storage.json",
    )
    parser.add_argument("--message", default="Hi, thanks for connecting. Hope you are doing well.", help="Message text")
    parser.add_argument("--headless", action="store_true", help="Run browser headless")
    parser.add_argument("--diagnose-only", action="store_true", help="Only inspect Message button structure")
    parser.add_argument(
        "--no-fail-on-missing",
        action="store_true",
        help="Do not exit non-zero when message click/composer/send is missing.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    result = send_linkedin_message_sync(
        profile_url=args.url,
        message_text=args.message,
        storage_state_path=args.storage,
        headless=args.headless,
        diagnose_only=args.diagnose_only,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))

    if args.no_fail_on_missing or args.diagnose_only:
        return

    if not bool(result.get("message_button_clicked")):
        logging.error("message button step not found/clicked; exiting with code 1")
        sys.exit(1)
    if not bool(result.get("message_composer_opened")):
        logging.error("message composer not opened; exiting with code 2")
        sys.exit(2)
    if not bool(result.get("message_sent")):
        logging.error("message send step not completed; exiting with code 3")
        sys.exit(3)


if __name__ == "__main__":
    main()

