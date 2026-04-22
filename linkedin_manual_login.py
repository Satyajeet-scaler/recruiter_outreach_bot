#!/usr/bin/env python3
"""
Open Chromium for manual LinkedIn login; save Playwright storage_state JSON.

Run from this repository root:
  python linkedin_manual_login.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


async def _main(storage_path: Path | None, *, upload: bool = True) -> None:
    try:
        from playwright.async_api import async_playwright
    except ModuleNotFoundError:
        print("Playwright is required. Install with: pip install playwright")
        sys.exit(1)

    from services.linkedin_session import get_linkedin_storage_path

    out = storage_path if storage_path is not None else get_linkedin_storage_path()
    out.parent.mkdir(parents=True, exist_ok=True)

    print(
        "Opening Chromium for manual LinkedIn login.\n"
        "Complete login/2FA in browser, then come back and press Enter to save session.\n"
    )

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        try:
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                locale="en-US",
            )
            page = await context.new_page()
            await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")

            await asyncio.to_thread(input, "Press Enter after successful login... ")
            await context.storage_state(path=str(out))
        finally:
            await browser.close()

    print(f"Saved session to {out}")
    if upload:
        _upload_session_to_railway_if_configured(out)


def _upload_session_to_railway_if_configured(storage_path: Path) -> None:
    """
    If LINKEDIN_SESSION_UPLOAD_URL and INTERNAL_TRIGGER_TOKEN are set, POST the
    JSON to Railway ``/internal/linkedin-session``.
    """
    url = (os.getenv("LINKEDIN_SESSION_UPLOAD_URL") or "").strip().rstrip("/")
    if url and "://" not in url:
        url = f"https://{url}"
    token = (os.getenv("INTERNAL_TRIGGER_TOKEN") or "").strip()
    if not url:
        print(
            "Tip: set LINKEDIN_SESSION_UPLOAD_URL (e.g. https://<app>/internal/linkedin-session) "
            "and INTERNAL_TRIGGER_TOKEN for automatic upload."
        )
        return
    if not token:
        print("LINKEDIN_SESSION_UPLOAD_URL set but INTERNAL_TRIGGER_TOKEN missing; skipping upload.")
        return

    parsed = urlparse(url)
    path = (parsed.path or "").rstrip("/") or "/"
    if path == "/" and "linkedin-session" not in url:
        url = f"{url}/internal/linkedin-session"
        print(f"Using upload URL: {url}")

    try:
        import requests
    except ImportError:
        print("requests is required for upload; pip install requests")
        sys.exit(1)

    try:
        data = json.loads(storage_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Could not read session file for upload: {exc}")
        sys.exit(1)

    try:
        response = requests.post(
            url,
            json=data,
            headers={
                "Content-Type": "application/json",
                "X-Internal-Trigger-Token": token,
            },
            timeout=60,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"Upload to Railway failed: {exc}")
        if getattr(exc, "response", None) is not None:
            print(getattr(exc.response, "text", "")[:2000])
        sys.exit(1)

    try:
        body = response.json()
    except json.JSONDecodeError:
        body = response.text
    print(f"Uploaded session to Railway: {body}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Save LinkedIn Playwright session (manual login)",
    )
    parser.add_argument(
        "output",
        nargs="?",
        type=Path,
        default=None,
        help="Output JSON path (default: LINKEDIN_STORAGE_PATH or data/linkedin_storage.json)",
    )
    parser.add_argument(
        "--no-upload",
        action="store_true",
        help="Skip Railway upload even when LINKEDIN_SESSION_UPLOAD_URL is set.",
    )
    args = parser.parse_args()
    asyncio.run(_main(args.output, upload=not args.no_upload))


if __name__ == "__main__":
    main()
