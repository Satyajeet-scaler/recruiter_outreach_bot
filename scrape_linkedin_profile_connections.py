#!/usr/bin/env python3
"""
CLI: detect LinkedIn profile connection degree (1st/2nd/3rd).

Requires Playwright session at data/linkedin_storage.json (see linkedin_manual_login.py).

  python scrape_linkedin_profile_connections.py "https://www.linkedin.com/in/example/"
  echo '["https://www.linkedin.com/in/example/"]' | python scrape_linkedin_profile_connections.py
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def main() -> None:
    from services.linkedin_recruiter.connections import (
        scrape_linkedin_profile_connection_degrees_sync,
    )

    logging.basicConfig(
        level=os.environ.get("APP_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    p = argparse.ArgumentParser(description="Detect LinkedIn profile connection degree")
    p.add_argument(
        "urls",
        nargs="*",
        help="LinkedIn profile URLs (or pass none and pipe a JSON array on stdin)",
    )
    p.add_argument(
        "--headless",
        default=os.environ.get("LINKEDIN_HEADLESS", "true"),
        help="true/false (default from LINKEDIN_HEADLESS or true)",
    )
    args = p.parse_args()

    if args.urls:
        urls = list(args.urls)
    else:
        raw = sys.stdin.read()
        urls = json.loads(raw) if raw.strip() else []

    if not urls:
        print("No URLs provided.", file=sys.stderr)
        sys.exit(1)

    headless = str(args.headless).lower() in ("1", "true", "yes")
    for row in scrape_linkedin_profile_connection_degrees_sync(urls, headless=headless):
        print(json.dumps(row, ensure_ascii=False))


if __name__ == "__main__":
    main()
