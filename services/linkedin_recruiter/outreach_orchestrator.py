"""Batch LinkedIn outreach orchestrator.

Takes a list of ``{profile_url, message_text}`` items, detects the connection
degree for each profile inside a single browser session, then routes to:

* **1st-degree** -> direct message via ``message_sender``
* **2nd/3rd-degree** -> connection request with ``message_text`` as the note
"""

from __future__ import annotations

import logging
import os
import socket
import time
from urllib.error import URLError
from pathlib import Path
from typing import Any, Sequence

import undetected_chromedriver as uc
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService

from services.linkedin_recruiter.connections import (
    _bucketize_connection_degree,
    parse_profile_connection_degree,
)
from services.linkedin_recruiter.connection_request_sender import (
    _send_connection_request_with_driver,
)
from services.linkedin_recruiter.ellipsis_menu_service import (
    DEFAULT_STORAGE_PATH,
    _detect_chrome_major_version,
    _inject_linkedin_cookies,
    _load_storage,
)
from services.linkedin_recruiter.message_sender import (
    _send_message_with_driver,
)

logger = logging.getLogger(__name__)

_LINKEDIN_NOTE_MAX_LEN = 200


def _launch_uc_chrome() -> uc.Chrome:
    options = uc.ChromeOptions()
    options.add_argument("--window-size=1440,2200")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-gpu")
    chrome_kwargs: dict[str, Any] = {"options": options}
    version_main = _detect_chrome_major_version()
    if version_main:
        chrome_kwargs["version_main"] = version_main
    if os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("PORT"):
        chrome_kwargs["browser_executable_path"] = "/usr/bin/chromium"
        chrome_kwargs["driver_executable_path"] = "/usr/bin/chromedriver"

    logger.info("orchestrator launching uc.Chrome with virtual display version_main=%s", version_main)
    try:
        return uc.Chrome(**chrome_kwargs)
    except (URLError, socket.gaierror) as exc:
        logger.warning("uc.Chrome launch failed due to network lookup; falling back to local chromedriver err=%s", exc)
    except Exception as exc:
        # In some environments uc wraps DNS/network failures in generic exceptions.
        if "Temporary failure in name resolution" not in str(exc):
            raise
        logger.warning("uc.Chrome launch failed with DNS resolution issue; falling back err=%s", exc)

    chrome_binary = "/usr/bin/google-chrome"
    if not os.path.exists(chrome_binary):
        chrome_binary = "/usr/bin/chromium"
    chromedriver_binary = "/usr/bin/chromedriver"
    if not os.path.exists(chromedriver_binary):
        raise RuntimeError("Could not launch browser: uc failed and /usr/bin/chromedriver not found.")

    selenium_options = webdriver.ChromeOptions()
    selenium_options.add_argument("--window-size=1440,2200")
    selenium_options.add_argument("--disable-dev-shm-usage")
    selenium_options.add_argument("--no-sandbox")
    selenium_options.add_argument("--disable-gpu")
    if os.path.exists(chrome_binary):
        selenium_options.binary_location = chrome_binary

    logger.info("orchestrator launching fallback selenium.Chrome binary=%s driver=%s", chrome_binary, chromedriver_binary)
    return webdriver.Chrome(
        service=ChromeService(executable_path=chromedriver_binary),
        options=selenium_options,
    )


def _detect_degree_from_page(driver: uc.Chrome) -> dict[str, Any]:
    """Detect connection degree using in-page JS first, then HTML parser fallback."""
    js = """
        const isVisible = (el) => {
            if (!el) return false;
            const r = el.getBoundingClientRect();
            const s = window.getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
        };
        const textOf = (el) => (el && (el.innerText || el.textContent) || '').replace(/\\s+/g, ' ').trim();
        const main = document.querySelector('main');
        const root = main || document.body;
        if (!root) return null;
        const spans = Array.from(
            root.querySelectorAll('span, div, li')
        ).filter(isVisible);
        const out = [];
        for (const el of spans) {
            const txt = textOf(el);
            if (!txt) continue;
            // Look for isolated degree tokens near the name/badge region.
            const m = txt.match(/\\b(1st|2nd|3rd\\+?)\\b/);
            if (!m) continue;
            // Skip obvious sidebar sections.
            const lower = txt.toLowerCase();
            if (lower.includes('more profiles for you') || lower.includes('people you may know')) continue;
            const rect = el.getBoundingClientRect();
            out.push({
                token: m[1],
                text: txt.slice(0, 160),
                y: rect.top,
            });
        }
        if (!out.length) return null;
        // Prefer tokens closest to the top of main content.
        out.sort((a, b) => a.y - b.y);
        return out.slice(0, 5);
    """
    js_result = None
    try:
        js_result = driver.execute_script(js)
    except Exception:
        js_result = None

    degree = None
    raw_indicator = None
    if isinstance(js_result, list) and js_result:
        tokens = [str(item.get("token", "")).strip() for item in js_result if isinstance(item, dict)]
        from services.linkedin_recruiter.connections import _pick_degree_from_tokens  # type: ignore

        resolved = _pick_degree_from_tokens(tokens)  # pragma: no cover - simple mapping
        if resolved:
            degree = resolved
            raw_indicator = ",".join(tokens)

    parsed = {}
    if degree is None:
        html = driver.page_source
        parsed = parse_profile_connection_degree(html)
        degree = parsed.get("connection_degree")
        raw_indicator = parsed.get("raw_indicator")

    bucket = _bucketize_connection_degree(degree if isinstance(degree, str) else None)
    return {
        "connection_degree": degree,
        "connection_bucket": bucket,
        "is_first_degree": degree == "1st",
        "is_second_or_third_degree": bucket == "2nd_or_3rd",
        "raw_indicator": raw_indicator,
    }


def _detect_pending_connection_request(driver: uc.Chrome) -> bool:
    """
    Detect whether a connection request is already pending.

    LinkedIn shows a visible 'Pending' control near the top-card CTAs for profiles
    where an invite was already sent.
    """
    js = """
        const isVisible = (el) => {
            if (!el) return false;
            const r = el.getBoundingClientRect();
            const s = window.getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
        };
        const norm = (t) => (t || '').replace(/\\s+/g,' ').trim().toLowerCase();
        const main = document.querySelector('main') || document;
        // Prefer top-card / CTA containers; avoid sidebar cards.
        const roots = Array.from(
            main.querySelectorAll(
                '.pv-top-card-v2-ctas, .pv-top-card-v2-section__container, .ph5.pb5, .mt2.relative, main section'
            )
        );
        roots.unshift(main);
        const seen = new WeakSet();
        for (const root of roots) {
            const nodes = Array.from(root.querySelectorAll('button, a[role=\"button\"], div[role=\"button\"], span'))
                .filter(isVisible);
            for (const el of nodes) {
                if (seen.has(el)) continue;
                seen.add(el);
                const txt = norm(el.innerText || el.textContent);
                const aria = norm(el.getAttribute && el.getAttribute('aria-label'));
                const title = norm(el.getAttribute && el.getAttribute('title'));
                // "Pending" is typically the button text itself.
                if (txt === 'pending' || aria.includes('pending') || title === 'pending') {
                    // Ensure it's in the main/top-card region, not "More profiles" list.
                    if (el.closest('.pv-top-card-v2-ctas, .pv-top-card-v2-section__container, main')) return true;
                }
            }
        }
        return false;
    """
    try:
        return bool(driver.execute_script(js))
    except Exception:
        return False


def _new_profile_result(profile_url: str) -> dict[str, Any]:
    return {
        "profile_url": profile_url,
        "connection_degree": None,
        "connection_bucket": None,
        "action_taken": "skipped",
        "success": False,
        "step_succeeded": None,
        "skip_reason": None,
        "error": None,
    }


def _compact_action_details(details: dict[str, Any], action: str) -> dict[str, Any]:
    if action == "message_sent":
        return {
            "message_button_clicked": bool(details.get("message_button_clicked")),
            "message_composer_opened": bool(details.get("message_composer_opened")),
            "message_filled": bool(details.get("message_filled")),
            "message_sent": bool(details.get("message_sent")),
            "message_modal_closed": bool(details.get("message_modal_closed")),
        }
    if action == "connection_request_sent":
        return {
            "connect_clicked": bool(details.get("connect_clicked")),
            "add_note_clicked": bool(details.get("add_note_clicked")),
            "send_clicked": bool(details.get("send_clicked")),
            "verify_now_clicked": bool(details.get("verify_now_clicked")),
        }
    return {}


def _execute_profile_action(
    driver: uc.Chrome,
    profile_url: str,
    message_text: str,
    *,
    bucket: str | None,
    timeout_s: int,
    debug: bool,
) -> tuple[str, bool, dict[str, Any]]:
    # For 2nd/3rd-degree profiles, skip if invite already pending.
    if bucket == "2nd_or_3rd" and _detect_pending_connection_request(driver):
        return "skipped", True, {"skip_reason": "already_pending"}

    if bucket == "1st":
        details = _send_message_with_driver(
            driver,
            profile_url,
            message_text,
            initial_wait_s=0,
            timeout_s=timeout_s,
            debug=debug,
        )
        return "message_sent", bool(details.get("message_sent")), details

    if bucket == "2nd_or_3rd":
        note = message_text[:_LINKEDIN_NOTE_MAX_LEN] if message_text else ""
        details = _send_connection_request_with_driver(
            driver,
            profile_url,
            note,
            initial_wait_s=0,
            timeout_s=timeout_s,
        )
        return "connection_request_sent", bool(details.get("send_clicked")), details

    return "skipped", False, {}


def run_outreach_batch_sync(
    items: Sequence[dict[str, str]],
    *,
    storage_state_path: str | Path = DEFAULT_STORAGE_PATH,
    initial_wait_s: float = 4.0,
    timeout_s: int = 25,
    wait_before_close_s: float = 10.0,
    debug: bool = False,
) -> list[dict[str, Any]]:
    """Process a batch of LinkedIn profiles in one browser session.

    Parameters
    ----------
    items:
        Each dict must contain ``profile_url`` and ``message_text``.
        ``message_text`` is sent as a direct message for 1st-degree connections
        and used as the connection-request note (truncated to 200 chars) for
        2nd/3rd-degree connections.
    storage_state_path:
        Path to the Playwright-format ``longin_storage.json``.
    initial_wait_s:
        Seconds to wait after navigating to each profile.
    timeout_s:
        Timeout for interactive element waits.
    wait_before_close_s:
        Seconds to wait before quitting the browser.

    Returns
    -------
    list[dict]
        One compact result dict per input item.
    """
    storage_path = Path(storage_state_path).expanduser().resolve()
    if not storage_path.is_file():
        raise FileNotFoundError(f"Storage JSON not found: {storage_path}")
    storage_data = _load_storage(storage_path)

    driver = _launch_uc_chrome()
    results: list[dict[str, Any]] = []
    try:
        injected = _inject_linkedin_cookies(driver, storage_data)
        logger.info("orchestrator cookies injected=%s", injected)

        for idx, item in enumerate(items, start=1):
            profile_url = item.get("profile_url", "")
            message_text = item.get("message_text", "")
            entry = _new_profile_result(profile_url)

            if not profile_url:
                entry["error"] = "missing profile_url"
                logger.error("outreach profile failed url=<missing> step=validate_input err=%s", entry["error"])
                results.append(entry)
                continue

            logger.info("outreach profile start idx=%s/%s url=%s", idx, len(items), profile_url)

            try:
                driver.get(profile_url)
                time.sleep(max(0.0, initial_wait_s))
                logger.info("outreach profile step=load_profile status=success url=%s", profile_url)

                degree_info = _detect_degree_from_page(driver)
                entry.update(degree_info)
                bucket = degree_info["connection_bucket"]
                logger.info(
                    "outreach profile step=detect_degree status=success url=%s degree=%s bucket=%s",
                    profile_url,
                    degree_info.get("connection_degree"),
                    bucket,
                )

                action, success, details = _execute_profile_action(
                    driver,
                    profile_url,
                    message_text,
                    bucket=bucket,
                    timeout_s=timeout_s,
                    debug=debug,
                )
                entry["action_taken"] = action
                entry["success"] = success
                entry["step_succeeded"] = action if success else None
                if action == "skipped":
                    skip_reason = None
                    if isinstance(details, dict):
                        skip_reason = details.get("skip_reason")
                    entry["skip_reason"] = skip_reason
                    if skip_reason == "already_pending":
                        logger.info(
                            "outreach profile step=route_action status=skipped url=%s reason=already_pending",
                            profile_url,
                        )
                    else:
                        entry["error"] = f"unknown connection degree: {degree_info.get('connection_degree')}"
                        logger.error(
                            "outreach profile failed url=%s step=route_action err=%s",
                            profile_url,
                            entry["error"],
                        )
                elif success:
                    logger.info(
                        "outreach profile step=%s status=success url=%s",
                        action,
                        profile_url,
                    )
                else:
                    entry["error"] = f"{action} failed"
                    logger.error(
                        "outreach profile failed url=%s step=%s err=%s",
                        profile_url,
                        action,
                        entry["error"],
                    )
                if debug:
                    entry["details"] = details
                else:
                    if action == "skipped" and isinstance(details, dict) and details.get("skip_reason"):
                        entry["details"] = {"skip_reason": details.get("skip_reason")}
                        compact = {}
                    else:
                        compact = _compact_action_details(details, action)
                    if compact:
                        entry["details"] = compact

            except Exception as exc:
                logger.exception("outreach profile failed url=%s step=exception", profile_url)
                entry["error"] = str(exc)

            results.append(entry)

        return results
    finally:
        if wait_before_close_s > 0:
            logger.info("orchestrator waiting %.1fs before closing browser", wait_before_close_s)
            time.sleep(wait_before_close_s)
        driver.quit()
