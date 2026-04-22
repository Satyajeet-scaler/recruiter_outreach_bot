#!/usr/bin/env python3
"""
Quick test: open LinkedIn profile with undetected-chromedriver and click ellipsis.

Defaults:
- URL: https://www.linkedin.com/in/chaitra-v-248672190/
- storage_state: /home/satyajeet/Desktop/jobs_scraper/job_scaper/data/linkedin_storage.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


DEFAULT_URL = "https://www.linkedin.com/in/chaitra-v-248672190/"
DEFAULT_STORAGE = "/home/satyajeet/Desktop/jobs_scraper/job_scaper/data/linkedin_storage.json"
DEFAULT_DEBUG_DIR = "/home/satyajeet/Desktop/jobs_scraper/recruiter_outreach_bot/debug_output"


def _load_storage(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict) or "cookies" not in data:
        raise ValueError("Expected Playwright storage_state JSON with cookies list.")
    return data


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


def _inject_linkedin_cookies(driver: uc.Chrome, storage_data: dict[str, Any]) -> int:
    driver.get("https://www.linkedin.com/")
    time.sleep(1.5)
    injected = 0
    for c in storage_data.get("cookies", []):
        domain = str(c.get("domain") or "").lower()
        if "linkedin.com" not in domain:
            continue
        try:
            driver.add_cookie(_to_selenium_cookie(c))
            injected += 1
        except Exception:
            # Some cookies may be rejected by Chrome if invalid for the loaded host.
            continue
    return injected


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


def _visible_button_report(driver: uc.Chrome, limit: int = 40) -> list[dict[str, Any]]:
    script = """
        const limit = arguments[0];
        const out = [];
        const isVisible = (el) => {
            const r = el.getBoundingClientRect();
            const s = window.getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== "none" && s.visibility !== "hidden";
        };
        const nodes = Array.from(document.querySelectorAll("button"));
        for (const el of nodes) {
            if (!isVisible(el)) continue;
            const r = el.getBoundingClientRect();
            out.push({
                text: (el.innerText || "").replace(/\\s+/g, " ").trim(),
                aria: el.getAttribute("aria-label") || "",
                testid: el.getAttribute("data-testid") || "",
                cls: el.className || "",
                x: Math.round(r.x),
                y: Math.round(r.y),
                w: Math.round(r.width),
                h: Math.round(r.height),
            });
            if (out.length >= limit) break;
        }
        return out;
    """
    return driver.execute_script(script, limit) or []


def _ellipsis_candidates_report(driver: uc.Chrome, limit: int = 25) -> list[dict[str, Any]]:
    script = """
        const limit = arguments[0];
        const out = [];
        const isVisible = (el) => {
            const r = el.getBoundingClientRect();
            const s = window.getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== "none" && s.visibility !== "hidden";
        };
        const nodes = Array.from(document.querySelectorAll("button, [role='button'], span, div, a"));
        for (const el of nodes) {
            const txt = (el.innerText || "").replace(/\\s+/g, " ").trim().toLowerCase();
            const aria = (el.getAttribute("aria-label") || "").toLowerCase();
            const raw = (el.innerText || "").trim();
            if (!isVisible(el)) continue;
            const looks = txt === "more" || raw === "…" || raw === "..." || aria.includes("more");
            if (!looks) continue;
            const r = el.getBoundingClientRect();
            out.push({
                tag: el.tagName.toLowerCase(),
                text: raw,
                aria: el.getAttribute("aria-label") || "",
                role: el.getAttribute("role") || "",
                testid: el.getAttribute("data-testid") || "",
                cls: el.className || "",
                x: Math.round(r.x),
                y: Math.round(r.y),
                w: Math.round(r.width),
                h: Math.round(r.height),
            });
            if (out.length >= limit) break;
        }
        return out;
    """
    return driver.execute_script(script, limit) or []


def _dump_debug_artifacts(driver: uc.Chrome, debug_dir: Path) -> tuple[Path, Path]:
    debug_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    screenshot_path = debug_dir / f"linkedin_profile_{stamp}.png"
    html_path = debug_dir / f"linkedin_profile_{stamp}.html"
    driver.save_screenshot(str(screenshot_path))
    html_path.write_text(driver.page_source, encoding="utf-8")
    return screenshot_path, html_path


def _click_ellipsis(driver: uc.Chrome, timeout_s: int = 20) -> bool:
    wait = WebDriverWait(driver, timeout_s)
    selectors = [
        "button[aria-label*='More'][aria-label*='actions']",
        "button[aria-label='More actions']",
        "main button[aria-label*='More']",
    ]

    for selector in selectors:
        count = len(driver.find_elements(By.CSS_SELECTOR, selector))
        print(f"[debug] selector={selector!r} count={count}")
        try:
            el = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, selector)))
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            time.sleep(0.4)
            try:
                el.click()
                print(f"[debug] click success via selenium selector={selector!r}")
            except Exception:
                driver.execute_script("arguments[0].click();", el)
                print(f"[debug] click success via js selector={selector!r}")
            return True
        except Exception as exc:
            print(f"[debug] click failed selector={selector!r} err={type(exc).__name__}: {exc}")
            continue

    # Broad fallback for text-based buttons.
    js = """
        const nodes = Array.from(document.querySelectorAll("button"));
        const isVisible = (el) => {
            const r = el.getBoundingClientRect();
            const s = window.getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== "none" && s.visibility !== "hidden";
        };
        for (const el of nodes) {
            const txt = (el.innerText || "").trim().toLowerCase();
            const aria = (el.getAttribute("aria-label") || "").toLowerCase();
            if (!isVisible(el)) continue;
            if (txt === "more" || aria.includes("more actions") || aria.includes("more")) {
                el.click();
                return true;
            }
        }
        return false;
    """
    fallback_clicked = bool(driver.execute_script(js))
    print(f"[debug] fallback text-based click={fallback_clicked}")
    return fallback_clicked


def main() -> None:
    parser = argparse.ArgumentParser(description="LinkedIn ellipsis click test with undetected-chromedriver")
    parser.add_argument("--url", default=DEFAULT_URL, help="LinkedIn profile URL")
    parser.add_argument("--storage", default=DEFAULT_STORAGE, help="Path to Playwright linkedin_storage.json")
    parser.add_argument("--wait-after-click", type=float, default=8.0, help="Seconds to keep browser open")
    parser.add_argument("--debug-dir", default=DEFAULT_DEBUG_DIR, help="Directory for screenshot/html debug dumps")
    args = parser.parse_args()

    storage_path = Path(args.storage).expanduser().resolve()
    if not storage_path.is_file():
        raise FileNotFoundError(f"Storage JSON not found: {storage_path}")

    data = _load_storage(storage_path)

    options = uc.ChromeOptions()
    options.add_argument("--window-size=1440,2200")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-gpu")

    chrome_kwargs: dict[str, Any] = {"options": options}
    version_main = _detect_chrome_major_version()
    if version_main:
        chrome_kwargs["version_main"] = version_main
    print(f"[debug] detected chrome major version={version_main}")
    if os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("PORT"):
        chrome_kwargs["browser_executable_path"] = "/usr/bin/chromium"
        chrome_kwargs["driver_executable_path"] = "/usr/bin/chromedriver"
    print(f"[debug] uc chrome kwargs={chrome_kwargs}")

    driver = uc.Chrome(**chrome_kwargs)
    try:
        print(f"Using storage: {storage_path}")
        injected = _inject_linkedin_cookies(driver, data)
        print(f"Injected LinkedIn cookies: {injected}")

        driver.get(args.url)
        time.sleep(4)
        print(f"[debug] landed url={driver.current_url}")
        print(f"[debug] page title={driver.title}")

        screenshot_path, html_path = _dump_debug_artifacts(driver, Path(args.debug_dir))
        print(f"[debug] screenshot saved={screenshot_path}")
        print(f"[debug] html saved={html_path}")

        buttons = _visible_button_report(driver, limit=50)
        print(f"[debug] visible button count(sample)={len(buttons)}")
        for i, b in enumerate(buttons[:20], start=1):
            print(
                f"[debug] button[{i}] text={b['text']!r} aria={b['aria']!r} "
                f"testid={b['testid']!r} pos=({b['x']},{b['y']},{b['w']},{b['h']})"
            )

        candidates = _ellipsis_candidates_report(driver, limit=30)
        print(f"[debug] ellipsis/more candidates={len(candidates)}")
        for i, c in enumerate(candidates, start=1):
            print(
                f"[debug] candidate[{i}] tag={c['tag']} text={c['text']!r} aria={c['aria']!r} "
                f"role={c['role']!r} testid={c['testid']!r} pos=({c['x']},{c['y']},{c['w']},{c['h']})"
            )

        clicked = _click_ellipsis(driver, timeout_s=20)
        print(f"Ellipsis clicked: {clicked}")
        if not clicked:
            print("[debug] no clicks were made for ellipsis candidates")
        print(f"Current URL: {driver.current_url}")
        time.sleep(max(0.0, args.wait_after_click))
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
