#!/usr/bin/env python3
"""CLI test script for LinkedIn profile connection-degree detection."""

from __future__ import annotations

import argparse
import json
import sys


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Detect LinkedIn profile connection degree (1st vs 2nd/3rd)."
    )
    parser.add_argument("url", help="LinkedIn profile URL (linkedin.com/in/...)")
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser in headless mode (default: visible browser).",
    )
    parser.add_argument(
        "--storage-state-path",
        default=None,
        help="Optional path to LinkedIn storage_state JSON.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        from services.linkedin_recruiter import find_connection_degree_by_profile_url_sync
    except ModuleNotFoundError as exc:
        if exc.name == "bs4":
            print(
                "Missing dependency: bs4\n"
                "Install with:\n"
                "  pip install beautifulsoup4 lxml",
                file=sys.stderr,
            )
            return 2
        raise

    result = find_connection_degree_by_profile_url_sync(
        args.url,
        storage_state_path=args.storage_state_path,
        headless=args.headless,
    )
    print(json.dumps(result, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
