"""Single-URL LinkedIn profile connection-degree service."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from services.linkedin_recruiter.connections import (
    get_linkedin_profile_connection_degree,
    get_linkedin_profile_connection_degree_sync,
)


async def find_connection_degree_by_profile_url(
    profile_url: str,
    *,
    storage_state_path: str | Path | None = None,
    headless: bool = True,
) -> dict[str, Any]:
    """
    Return connection degree details for one LinkedIn profile URL.

    Output keys include:
    - connection_degree: 1st/2nd/3rd (or None)
    - connection_bucket: 1st or 2nd_or_3rd (or None)
    - is_first_degree: bool
    - is_second_or_third_degree: bool
    """
    return await get_linkedin_profile_connection_degree(
        profile_url,
        storage_state_path=storage_state_path,
        headless=headless,
        strict_profile_url=True,
    )


def find_connection_degree_by_profile_url_sync(
    profile_url: str,
    *,
    storage_state_path: str | Path | None = None,
    headless: bool = True,
) -> dict[str, Any]:
    """Sync version of :func:`find_connection_degree_by_profile_url`."""
    return get_linkedin_profile_connection_degree_sync(
        profile_url,
        storage_state_path=storage_state_path,
        headless=headless,
        strict_profile_url=True,
    )
