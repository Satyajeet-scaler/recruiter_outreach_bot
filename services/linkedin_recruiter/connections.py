"""LinkedIn recruiter profile connection-degree detection (1st/2nd/3rd)."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Sequence

from bs4 import BeautifulSoup
import undetected_chromedriver as uc

from services.linkedin_recruiter.ellipsis_menu_service import (
    DEFAULT_STORAGE_PATH,
    _detect_chrome_major_version,
    _inject_linkedin_cookies,
    _load_storage,
)

logger = logging.getLogger(__name__)

_PROFILE_URL_RE = re.compile(r"^https?://(?:[\w-]+\.)?linkedin\.com/in/[^?\s#]+", re.I)
_DEGREE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("1st", re.compile(r"\b1st(?:\s*degree)?\s*connection\b", re.I)),
    ("2nd", re.compile(r"\b2nd(?:\s*degree)?\s*connection\b", re.I)),
    ("3rd", re.compile(r"\b3rd(?:\+|\s*degree)?\s*connection\b", re.I)),
)
_OUT_OF_NETWORK_RE = re.compile(r"\bout of network\b", re.I)
_FALLBACK_DEGREE_TOKEN_RE = re.compile(r"\b(1st|2nd|3rd\+?)\b", re.I)
_CONNECTION_CONTEXT_RE = re.compile(r"\bconnection(s)?\b", re.I)


def is_linkedin_profile_url(url: str) -> bool:
    """True if URL looks like a LinkedIn profile URL."""
    return bool(_PROFILE_URL_RE.match((url or "").strip()))


def _normalize_profile_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    return raw.split("?", 1)[0].split("#", 1)[0]


def _profile_connection_text_candidates(soup: BeautifulSoup) -> list[str]:
    """Collect top-card snippets likely to contain degree badge (avoid side panels)."""
    chunks: list[str] = []
    selectors = (
        "span.dist-value",
        ".pv-top-card-v2-section__container",
        ".pv-top-card-v2-ctas",
        ".pv-text-details__left-panel",
        ".ph5.pb5",
        ".mt2.relative",
        "main section.artdeco-card",
    )
    for selector in selectors:
        for node in soup.select(selector):
            text = node.get_text(" ", strip=True)
            if text:
                chunks.append(text)
    return chunks


def _pick_degree_from_tokens(tokens: list[str]) -> str | None:
    """Resolve conflicting degree tokens conservatively for outreach routing."""
    if not tokens:
        return None
    normalized = [t.lower().replace("+", "") for t in tokens]
    uniq = set(normalized)
    # If mixed badges appear (e.g. hidden/flattened text includes 1st + 2nd),
    # prefer non-1st to avoid messaging a non-1st profile.
    if "3rd" in uniq:
        return "3rd"
    if "2nd" in uniq:
        return "2nd"
    if "1st" in uniq:
        return "1st"
    return None


def parse_profile_connection_degree(html: str) -> dict[str, str | bool | None]:
    """Parse LinkedIn profile HTML and infer connection degree."""
    soup = BeautifulSoup(html, "lxml")
    candidates = _profile_connection_text_candidates(soup)

    # Trust only top-card context first; avoid matching sidebar/recommendation text.
    for text in candidates:
        for degree, pattern in _DEGREE_PATTERNS:
            if pattern.search(text):
                return {
                    "connection_degree": degree,
                    "is_first_degree": degree == "1st",
                    "raw_indicator": degree,
                }
        if _OUT_OF_NETWORK_RE.search(text):
            return {
                "connection_degree": "out_of_network",
                "is_first_degree": False,
                "raw_indicator": "out_of_network",
            }
        fallback = _FALLBACK_DEGREE_TOKEN_RE.search(text)
        if fallback:
            degree = fallback.group(1).replace("+", "")
            return {
                "connection_degree": degree,
                "is_first_degree": degree == "1st",
                "raw_indicator": fallback.group(1),
            }

    # Fallback: inspect only the top part of <main> where the top-card lives.
    # This avoids sidebar recommendation noise while still catching bare "1st".
    main = soup.select_one("main")
    if main is not None:
        main_text = main.get_text(" ", strip=True)
        top_main_text = main_text[:2500]
        top_tokens = [m.group(1) for m in _FALLBACK_DEGREE_TOKEN_RE.finditer(top_main_text[:450])]
        resolved_top_token = _pick_degree_from_tokens(top_tokens)
        if resolved_top_token:
            return {
                "connection_degree": resolved_top_token,
                "is_first_degree": resolved_top_token == "1st",
                "raw_indicator": ",".join(top_tokens[:6]),
            }
        for degree, pattern in _DEGREE_PATTERNS:
            if pattern.search(top_main_text):
                return {
                    "connection_degree": degree,
                    "is_first_degree": degree == "1st",
                    "raw_indicator": degree,
                }
        if _OUT_OF_NETWORK_RE.search(top_main_text):
            return {
                "connection_degree": "out_of_network",
                "is_first_degree": False,
                "raw_indicator": "out_of_network",
            }
        fallback = _FALLBACK_DEGREE_TOKEN_RE.search(top_main_text)
        if fallback:
            degree = fallback.group(1).replace("+", "")
            return {
                "connection_degree": degree,
                "is_first_degree": degree == "1st",
                "raw_indicator": fallback.group(1),
            }

    # Conservative fallback: use full page only if "connection" context is present nearby.
    page_text = soup.get_text(" ", strip=True)
    if _CONNECTION_CONTEXT_RE.search(page_text):
        for degree, pattern in _DEGREE_PATTERNS:
            if pattern.search(page_text):
                return {
                    "connection_degree": degree,
                    "is_first_degree": degree == "1st",
                    "raw_indicator": degree,
                }
        if _OUT_OF_NETWORK_RE.search(page_text):
            return {
                "connection_degree": "out_of_network",
                "is_first_degree": False,
                "raw_indicator": "out_of_network",
            }

    return {
        "connection_degree": None,
        "is_first_degree": False,
        "raw_indicator": None,
    }


def _bucketize_connection_degree(connection_degree: str | None) -> str | None:
    """Map detailed LinkedIn degree to high-level bucket requested by callers."""
    if connection_degree == "1st":
        return "1st"
    if connection_degree in {"2nd", "3rd"}:
        return "2nd_or_3rd"
    return None


async def scrape_linkedin_profile_connection_degrees(
    profile_urls: Sequence[str],
    *,
    storage_state_path: str | Path | None = None,
    timeout_ms: float = 60_000.0,
    force_fail_timeout_s: float = 15.0,
    recycle_every: int = 25,
    hydration_wait_s: float = 5.0,
    retry_count: int = 3,
    retry_base_delay_s: float = 1.0,
    headless: bool = True,
    strict_profile_urls: bool = False,
) -> list[dict[str, Any]]:
    """Fetch LinkedIn profile URLs and detect connection degree for each URL."""
    if strict_profile_urls:
        bad = [u for u in profile_urls if not is_linkedin_profile_url(u)]
        if bad:
            raise ValueError(f"Not LinkedIn profile URLs (strict_profile_urls): {bad!r}")

    state_path = Path(storage_state_path or DEFAULT_STORAGE_PATH).expanduser().resolve()
    if not state_path.is_file():
        raise FileNotFoundError(f"Storage JSON not found: {state_path}")
    storage_data = _load_storage(state_path)

    results: list[dict[str, Any]] = []
    indexed_urls: list[str] = []
    for url in profile_urls:
        normalized = _normalize_profile_url(url)
        if not is_linkedin_profile_url(normalized):
            results.append(
                {
                    "url": url,
                    "error": "skipped: not a linkedin.com/in URL",
                    "connection_degree": None,
                    "is_first_degree": False,
                    "raw_indicator": None,
                }
            )
            continue
        indexed_urls.append(normalized)

    by_url: dict[str, dict[str, Any]] = {}
    if indexed_urls:
        options = uc.ChromeOptions()
        options.add_argument("--window-size=1440,2200")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-gpu")
        if headless:
            options.add_argument("--headless=new")

        chrome_kwargs: dict[str, Any] = {"options": options}
        version_main = _detect_chrome_major_version()
        if version_main:
            chrome_kwargs["version_main"] = version_main
        if os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("PORT"):
            chrome_kwargs["browser_executable_path"] = "/usr/bin/chromium"
            chrome_kwargs["driver_executable_path"] = "/usr/bin/chromedriver"

        driver = uc.Chrome(**chrome_kwargs)
        try:
            injected = _inject_linkedin_cookies(driver, storage_data)
            logger.info("injected linkedin cookies=%s", injected)
            page_timeout_ms = int(timeout_ms) if timeout_ms > 0 else 60_000
            driver.set_page_load_timeout(max(15, page_timeout_ms // 1000))

            for idx, url in enumerate(indexed_urls, start=1):
                try:
                    driver.get(url)
                    time.sleep(max(0.0, hydration_wait_s))
                    by_url[url] = {"url": url, "html": driver.page_source}
                except Exception as exc:
                    by_url[url] = {"url": url, "error": str(exc)}

                if recycle_every > 0 and idx % recycle_every == 0 and idx < len(indexed_urls):
                    driver.quit()
                    driver = uc.Chrome(**chrome_kwargs)
                    _inject_linkedin_cookies(driver, storage_data)
                    driver.set_page_load_timeout(max(15, page_timeout_ms // 1000))
        finally:
            driver.quit()

    for url in indexed_urls:
        item = by_url.get(url, {"url": url, "error": "missing fetch result"})
        try:
            if "error" in item:
                raise RuntimeError(item["error"])
            parsed = parse_profile_connection_degree(item["html"])
            results.append({"url": url, **parsed})
        except Exception as exc:
            logger.exception("LinkedIn profile degree parse failed: %s", url)
            results.append(
                {
                    "url": url,
                    "error": str(exc),
                    "connection_degree": None,
                    "is_first_degree": False,
                    "raw_indicator": None,
                }
            )

    return results


def scrape_linkedin_profile_connection_degrees_sync(
    profile_urls: Sequence[str],
    *,
    storage_state_path: str | Path | None = None,
    timeout_ms: float = 60_000.0,
    force_fail_timeout_s: float = 15.0,
    recycle_every: int = 25,
    hydration_wait_s: float = 5.0,
    retry_count: int = 3,
    retry_base_delay_s: float = 1.0,
    headless: bool = True,
    strict_profile_urls: bool = False,
) -> list[dict[str, Any]]:
    """Sync wrapper around :func:`scrape_linkedin_profile_connection_degrees`."""
    return asyncio.run(
        scrape_linkedin_profile_connection_degrees(
            profile_urls,
            storage_state_path=storage_state_path,
            timeout_ms=timeout_ms,
            force_fail_timeout_s=force_fail_timeout_s,
            recycle_every=recycle_every,
            hydration_wait_s=hydration_wait_s,
            retry_count=retry_count,
            retry_base_delay_s=retry_base_delay_s,
            headless=headless,
            strict_profile_urls=strict_profile_urls,
        )
    )


async def get_linkedin_profile_connection_degree(
    profile_url: str,
    *,
    storage_state_path: str | Path | None = None,
    timeout_ms: float = 60_000.0,
    force_fail_timeout_s: float = 15.0,
    recycle_every: int = 25,
    hydration_wait_s: float = 5.0,
    retry_count: int = 3,
    retry_base_delay_s: float = 1.0,
    headless: bool = True,
    strict_profile_url: bool = True,
) -> dict[str, Any]:
    """
    Detect connection degree for one LinkedIn profile URL.

    Returns both raw degree (`1st`, `2nd`, `3rd`) and high-level bucket
    (`1st`, `2nd_or_3rd`).
    """
    items = await scrape_linkedin_profile_connection_degrees(
        [profile_url],
        storage_state_path=storage_state_path,
        timeout_ms=timeout_ms,
        force_fail_timeout_s=force_fail_timeout_s,
        recycle_every=recycle_every,
        hydration_wait_s=hydration_wait_s,
        retry_count=retry_count,
        retry_base_delay_s=retry_base_delay_s,
        headless=headless,
        strict_profile_urls=strict_profile_url,
    )
    result = items[0] if items else {"url": profile_url, "error": "no result"}
    connection_degree = result.get("connection_degree")
    bucket = _bucketize_connection_degree(connection_degree if isinstance(connection_degree, str) else None)
    return {
        **result,
        "connection_bucket": bucket,
        "is_second_or_third_degree": bucket == "2nd_or_3rd",
    }


def get_linkedin_profile_connection_degree_sync(
    profile_url: str,
    *,
    storage_state_path: str | Path | None = None,
    timeout_ms: float = 60_000.0,
    force_fail_timeout_s: float = 15.0,
    recycle_every: int = 25,
    hydration_wait_s: float = 5.0,
    retry_count: int = 3,
    retry_base_delay_s: float = 1.0,
    headless: bool = True,
    strict_profile_url: bool = True,
) -> dict[str, Any]:
    """Sync wrapper for single-URL connection-degree detection."""
    return asyncio.run(
        get_linkedin_profile_connection_degree(
            profile_url,
            storage_state_path=storage_state_path,
            timeout_ms=timeout_ms,
            force_fail_timeout_s=force_fail_timeout_s,
            recycle_every=recycle_every,
            hydration_wait_s=hydration_wait_s,
            retry_count=retry_count,
            retry_base_delay_s=retry_base_delay_s,
            headless=headless,
            strict_profile_url=strict_profile_url,
        )
    )
