#!/usr/bin/env python3
"""Run ellipsis-menu service for a LinkedIn profile URL."""

from __future__ import annotations

import argparse
import json
import logging

from services.linkedin_recruiter.ellipsis_menu_service import (
    click_profile_ellipsis_and_get_menu_options_sync,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Click LinkedIn profile ellipsis and print menu options")
    parser.add_argument("--url", required=True, help="LinkedIn profile URL")
    parser.add_argument(
        "--storage",
        default="/home/satyajeet/Desktop/jobs_scraper/job_scaper/data/linkedin_storage.json",
        help="Path to Playwright linkedin_storage.json",
    )
    parser.add_argument("--headless", action="store_true", help="Run browser headless")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    result = click_profile_ellipsis_and_get_menu_options_sync(
        args.url,
        storage_state_path=args.storage,
        headless=args.headless,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
