"""LinkedIn storage-state helpers for manual login/session upload."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SERVICE_ROOT = Path(__file__).resolve().parent.parent


def get_linkedin_storage_path() -> Path:
    """
    Path for Playwright-style ``storage_state`` JSON.

    Override with ``LINKEDIN_STORAGE_PATH`` (e.g. Railway volume path).
    """
    raw = os.environ.get("LINKEDIN_STORAGE_PATH")
    if raw:
        return Path(raw).expanduser().resolve()
    return _SERVICE_ROOT / "data" / "linkedin_storage.json"


def validate_playwright_storage_state(data: Any) -> dict[str, Any]:
    """Ensure payload resembles Playwright ``storage_state`` JSON."""
    if not isinstance(data, dict):
        raise ValueError("Body must be a JSON object")
    cookies = data.get("cookies")
    if cookies is None:
        raise ValueError(
            "Missing 'cookies'; expected Playwright storage_state "
            "(from context.storage_state() export)."
        )
    if not isinstance(cookies, list):
        raise ValueError("'cookies' must be a list")
    return data


def save_linkedin_storage_state_json(data: dict[str, Any]) -> Path:
    """Write validated storage state to :func:`get_linkedin_storage_path`."""
    validate_playwright_storage_state(data)
    path = get_linkedin_storage_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    logger.info(
        "Saved LinkedIn storage state to %s (%d cookies)",
        path,
        len(data.get("cookies", [])),
    )
    return path
