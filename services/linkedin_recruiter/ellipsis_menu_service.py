"""LinkedIn profile ellipsis click + menu options extraction using undetected-chromedriver."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from services.linkedin_session import get_linkedin_storage_path

logger = logging.getLogger(__name__)

# Single source of truth for LinkedIn session storage path.
# This ensures manual login save path and runtime read path always match,
# including Railway volume overrides via LINKEDIN_STORAGE_PATH.
DEFAULT_STORAGE_PATH = str(get_linkedin_storage_path())


def _detect_chrome_major_version() -> int | None:
    candidates = [
        ["google-chrome", "--version"],
        ["google-chrome-stable", "--version"],
        ["chromium", "--version"],
        ["chromium-browser", "--version"],
        ["/usr/bin/chromium", "--version"],
    ]
    for cmd in candidates:
        try:
            out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True).strip()
            match = re.search(r"(\d+)\.\d+\.\d+\.\d+", out)
            if match:
                return int(match.group(1))
        except Exception:
            continue
    return None


def _resolve_browser_driver_paths() -> tuple[str | None, str | None]:
    """
    Resolve browser + chromedriver paths across macOS/Linux with env overrides.

    Env overrides (highest priority):
    - CHROME_BINARY_PATH (or CHROMIUM_BINARY_PATH)
    - CHROMEDRIVER_PATH
    """
    browser_env = (os.getenv("CHROME_BINARY_PATH") or os.getenv("CHROMIUM_BINARY_PATH") or "").strip()
    driver_env = (os.getenv("CHROMEDRIVER_PATH") or "").strip()
    browser_path = browser_env if browser_env and Path(browser_env).exists() else None
    driver_path = driver_env if driver_env and Path(driver_env).exists() else None

    if not browser_path:
        browser_candidates: list[str] = []
        if sys.platform == "darwin":
            browser_candidates = [
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Chromium.app/Contents/MacOS/Chromium",
                "/Applications/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
                "/opt/homebrew/bin/chromium",
                "/opt/homebrew/bin/google-chrome",
                "/usr/local/bin/chromium",
                "/usr/local/bin/google-chrome",
            ]
        else:
            browser_candidates = [
                "/usr/bin/google-chrome",
                "/usr/bin/google-chrome-stable",
                "/usr/bin/chromium",
                "/usr/bin/chromium-browser",
            ]
        for cmd in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
            found = shutil.which(cmd)
            if found:
                browser_candidates.append(found)
        browser_path = next((p for p in browser_candidates if Path(p).exists()), None)

    if not driver_path:
        # We intentionally do not suggest system paths like /usr/bin/chromedriver here.
        # If CHROMEDRIVER_PATH is not set, we return None so that undetected-chromedriver
        # can download and manage its own writable version in the user's home directory.
        pass

    return browser_path, driver_path


def _build_uc_chrome_kwargs(options: uc.ChromeOptions, *, version_main: int | None) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"options": options}
    if version_main:
        kwargs["version_main"] = version_main

    browser_path, driver_path = _resolve_browser_driver_paths()
    if browser_path:
        kwargs["browser_executable_path"] = browser_path
    if driver_path:
        kwargs["driver_executable_path"] = driver_path
    return kwargs


def _to_selenium_cookie(cookie: dict[str, Any]) -> dict[str, Any]:
    out = {
        "name": cookie.get("name"),
        "value": cookie.get("value"),
        "domain": cookie.get("domain"),
        "path": cookie.get("path", "/"),
        "secure": bool(cookie.get("secure", False)),
        "httpOnly": bool(cookie.get("httpOnly", False)),
    }
    expires = cookie.get("expires")
    if isinstance(expires, (int, float)) and expires > 0:
        out["expiry"] = int(expires)
    same_site = cookie.get("sameSite")
    if same_site in {"Strict", "Lax", "None"}:
        out["sameSite"] = same_site
    return out


def _load_storage(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "cookies" not in data:
        raise ValueError("Expected Playwright storage_state JSON with cookies list.")
    return data


def _inject_linkedin_cookies(driver: uc.Chrome, storage_data: dict[str, Any]) -> int:
    driver.get("https://www.linkedin.com/")
    time.sleep(1.5)
    injected = 0
    for cookie in storage_data.get("cookies", []):
        domain = str(cookie.get("domain") or "").lower()
        if "linkedin.com" not in domain:
            continue
        try:
            driver.add_cookie(_to_selenium_cookie(cookie))
            injected += 1
        except Exception:
            continue
    return injected


def _click_profile_ellipsis(driver: uc.Chrome, timeout_s: int = 20) -> tuple[bool, str | None]:
    wait = WebDriverWait(driver, timeout_s)
    selectors = [
        "button[aria-label*='More'][aria-label*='actions']",
        "button[aria-label='More actions']",
        "main button[aria-label*='More']",
    ]
    for selector in selectors:
        count = len(driver.find_elements(By.CSS_SELECTOR, selector))
        logger.info("ellipsis selector=%r count=%s", selector, count)
        if count <= 0:
            continue
        try:
            el = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, selector)))
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            time.sleep(0.35)
            try:
                el.click()
            except Exception:
                driver.execute_script("arguments[0].click();", el)
            logger.info("ellipsis click success selector=%r", selector)
            return True, selector
        except Exception as exc:
            logger.warning("ellipsis click failed selector=%r err=%s", selector, exc)
    return False, None


def _extract_visible_menu_options(driver: uc.Chrome) -> list[str]:
    js = """
        const isVisible = (el) => {
            const r = el.getBoundingClientRect();
            const s = window.getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
        };
        const nodes = Array.from(document.querySelectorAll(
            "[role='menu'] [role='menuitem'], [role='menu'] button, [role='menu'] a, " +
            "div[role='dialog'] [role='menuitem'], .artdeco-dropdown__content-inner button, .artdeco-dropdown__content-inner a"
        ));
        const values = [];
        for (const el of nodes) {
            if (!isVisible(el)) continue;
            const txt = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
            if (txt) values.push(txt);
        }
        return values;
    """
    raw = driver.execute_script(js) or []
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        text = str(item).strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def click_profile_ellipsis_and_get_menu_options_sync(
    profile_url: str,
    *,
    storage_state_path: str | Path = DEFAULT_STORAGE_PATH,
    headless: bool = False,
    initial_wait_s: float = 4.0,
    post_click_wait_s: float = 1.0,
    wait_before_close_s: float = 10.0,
) -> dict[str, Any]:
    """
    Open LinkedIn profile, click the ellipsis button and return visible menu options.
    """
    storage_path = Path(storage_state_path).expanduser().resolve()
    if not storage_path.is_file():
        raise FileNotFoundError(f"Storage JSON not found: {storage_path}")
    storage_data = _load_storage(storage_path)

    options = uc.ChromeOptions()
    options.add_argument("--window-size=1440,2200")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-gpu")
    if headless:
        options.add_argument("--headless=new")

    version_main = _detect_chrome_major_version()
    chrome_kwargs = _build_uc_chrome_kwargs(options, version_main=version_main)

    logger.info("launching uc Chrome headless=%s version_main=%s", headless, version_main)
    driver = uc.Chrome(**chrome_kwargs)
    try:
        injected = _inject_linkedin_cookies(driver, storage_data)
        logger.info("injected linkedin cookies=%s", injected)
        driver.get(profile_url)
        time.sleep(max(0.0, initial_wait_s))

        clicked, selector = _click_profile_ellipsis(driver)
        if clicked:
            time.sleep(max(0.0, post_click_wait_s))
        options_list = _extract_visible_menu_options(driver) if clicked else []

        logger.info("menu options count=%s clicked=%s selector=%s", len(options_list), clicked, selector)
        for idx, option in enumerate(options_list, start=1):
            logger.info("menu option[%s]=%s", idx, option)

        return {
            "profile_url": profile_url,
            "clicked": clicked,
            "selector_used": selector,
            "menu_options": options_list,
            "cookie_count_injected": injected,
            "current_url": driver.current_url,
            "page_title": driver.title,
        }
    finally:
        if wait_before_close_s > 0:
            logger.info("waiting %.1fs before closing browser", wait_before_close_s)
            time.sleep(wait_before_close_s)
        driver.quit()


async def click_profile_ellipsis_and_get_menu_options(
    profile_url: str,
    *,
    storage_state_path: str | Path = DEFAULT_STORAGE_PATH,
    headless: bool = False,
    initial_wait_s: float = 4.0,
    post_click_wait_s: float = 1.0,
    wait_before_close_s: float = 10.0,
) -> dict[str, Any]:
    """Async wrapper for sync ellipsis-menu extraction."""
    return click_profile_ellipsis_and_get_menu_options_sync(
        profile_url,
        storage_state_path=storage_state_path,
        headless=headless,
        initial_wait_s=initial_wait_s,
        post_click_wait_s=post_click_wait_s,
        wait_before_close_s=wait_before_close_s,
    )
