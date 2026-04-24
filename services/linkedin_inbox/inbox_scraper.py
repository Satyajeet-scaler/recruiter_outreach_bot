"""Basic LinkedIn inbox scraper bootstrap.

Current scope:
- Launch browser.
- Login with LinkedIn credentials.
- Detect the floating Messaging widget in the bottom area.

This file is intentionally minimal so we can iteratively add:
- inbox thread scraping
- new message detection
- context building
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import undetected_chromedriver as uc
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from services.linkedin_recruiter.ellipsis_menu_service import (
    _build_uc_chrome_kwargs,
    _detect_chrome_major_version,
    _inject_linkedin_cookies,
    _load_storage,
)
from services.context_builder import ContextEvent
from services.context_builder.sheet_store import (
    DEFAULT_INTENT,
    append_context_row_from_env,
)
from services.db.recruiter_store import get_recruiter_id_by_linkedin_url
from services.db.conversation_store import (
    get_conversation_by_recruiter_id, 
    upsert_conversation, 
)
from services.db.linkedin_pm_sender_store import upsert_linkedin_pm_sender
from services.db.message_store import save_message, get_messages_by_conversation, update_message_delivery_status
from services.db.models import RecruiterConversation, ConversationMessage, OwnerType, DeliveryStatus
from services.intent_engine.intent_service import process_latest_message_intent
from services.linkedin_inbox.profile_resolver import resolve_recruiter_profile_url

logger = logging.getLogger(__name__)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_SESSION_PATH = _PROJECT_ROOT / "data" / "linkedin_storage.json"


@dataclass(slots=True)
class InboxScraperConfig:
    """Runtime config for basic scraper bootstrap."""

    headless: bool = False
    storage_state_path: str = str(_DEFAULT_SESSION_PATH)
    login_timeout_s: int = 45
    post_login_wait_s: float = 4.0
    wait_before_close_s: float = 2.0
    linkedin_home_url: str = "https://www.linkedin.com/feed/"
    watcher_mode: bool = False
    watch_interval_s: int = 60


@dataclass(slots=True)
class FloatingMessagingState:
    """UI state for the floating messaging widget in LinkedIn."""

    found: bool
    visible: bool
    widget_count: int
    has_badge: bool
    expanded_panel_visible: bool
    selected_selector: str = ""
    selected_rect: dict[str, int] | None = None
    debug_reason: str = ""


@dataclass(slots=True)
class FloatingMessagingClickResult:
    """Result of attempting to click/open the floating messaging widget."""

    clicked: bool
    click_strategy: str = ""
    open_state_after_click: bool = False
    debug_reason: str = ""


@dataclass(slots=True)
class MessagingConversationSnapshot:
    """Single conversation row extracted from messaging overlay."""

    profile_name: str
    profile_url: str
    snippet: str
    timestamp_text: str
    unread: bool
    unread_reason: str = ""


def _build_driver(*, headless: bool) -> uc.Chrome:
    """Create a browser instance with stable defaults."""
    options = uc.ChromeOptions()
    options.add_argument("--window-size=1440,2200")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-gpu")
    if headless:
        options.add_argument("--headless=new")

    version_main = _detect_chrome_major_version()
    chrome_kwargs = _build_uc_chrome_kwargs(options, version_main=version_main)
    driver = uc.Chrome(**chrome_kwargs)
    driver.set_page_load_timeout(60)
    return driver


def _wait_document_ready(driver: uc.Chrome, timeout_s: int = 30) -> None:
    WebDriverWait(driver, timeout_s).until(
        lambda d: d.execute_script("return document.readyState") == "complete"
    )


def _inject_cookies_with_retry(
    driver: uc.Chrome,
    storage_data: dict[str, Any],
    *,
    attempts: int = 3,
) -> int:
    """Inject cookies with retries to handle transient renderer timeouts."""
    last_exc: Exception | None = None
    for idx in range(1, max(1, attempts) + 1):
        try:
            return _inject_linkedin_cookies(driver, storage_data)
        except TimeoutException as exc:
            last_exc = exc
            logger.warning("cookie injection timeout attempt=%s/%s err=%s", idx, attempts, exc)
            try:
                # Stop pending load and retry from a clean navigation cycle.
                driver.execute_script("window.stop();")
            except Exception:
                pass
            time.sleep(min(2.5 * idx, 6.0))
        except Exception as exc:
            last_exc = exc
            logger.warning("cookie injection failed attempt=%s/%s err=%s", idx, attempts, exc)
            time.sleep(min(1.5 * idx, 4.0))
    raise RuntimeError("Failed to inject LinkedIn cookies after retries") from last_exc


def _is_logged_in_shell_present(driver: uc.Chrome) -> bool:
    """Best-effort check for logged-in LinkedIn shell across UI variants."""
    js = """
        const href = (window.location && window.location.href) ? window.location.href : '';
        const title = (document.title || '').toLowerCase();
        const lower = href.toLowerCase();
        const blocked = (
            lower.includes('/login') ||
            lower.includes('/checkpoint') ||
            lower.includes('/challenge') ||
            lower.includes('/signup')
        );
        if (blocked) return false;

        const titleLooksLoggedOut = (
            title.includes('login') ||
            title.includes('sign in') ||
            title.includes('security verification')
        );

        const selectors = [
            'header.global-nav',
            '#global-nav',
            '.global-nav',
            "nav[aria-label*='Primary Navigation']",
            "input[placeholder='Search']",
            ".share-box-feed-entry__top-bar",
            '#msg-overlay',
            '.msg-overlay-container',
            'aside.msg-overlay-list-bubble',
            '.msg-overlay-bubble-header__badge-container',
            '.feed-identity-module',
            'main[role="main"]'
        ];
        const isVisible = (el) => {
            if (!el) return false;
            const r = el.getBoundingClientRect();
            const s = window.getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
        };
        let visibleSignals = 0;
        for (const sel of selectors) {
            const el = document.querySelector(sel);
            if (isVisible(el)) visibleSignals += 1;
        }
        // Consider page logged-in when multiple surface signals are visible.
        return !titleLooksLoggedOut && visibleSignals >= 2;
    """
    try:
        return bool(driver.execute_script(js))
    except Exception:
        return False


def _is_authwall_url(url: str) -> bool:
    lower = (url or "").lower()
    return any(
        token in lower
        for token in ("/login", "/checkpoint", "/challenge", "/signup", "/authwall")
    )


def _linkedin_credentials_from_env() -> tuple[str, str]:
    """Read login credentials from env vars.

    Required vars:
    - LINKEDIN_EMAIL
    - LINKEDIN_PASSWORD
    """
    email = os.environ.get("LINKEDIN_EMAIL", "").strip()
    password = os.environ.get("LINKEDIN_PASSWORD", "").strip()
    if not email or not password:
        raise ValueError(
            "LinkedIn credentials not found. Set LINKEDIN_EMAIL and LINKEDIN_PASSWORD."
        )
    return email, password


def login_linkedin_with_credentials(
    driver: uc.Chrome,
    *,
    email: str,
    password: str,
    timeout_s: int = 45,
) -> None:
    """Open login page and sign in with username/password."""
    logger.info("opening linkedin login page")
    driver.get("https://www.linkedin.com/login")
    _wait_document_ready(driver)

    logger.info("filling linkedin credentials")
    email_input = WebDriverWait(driver, timeout_s).until(
        EC.presence_of_element_located((By.ID, "username"))
    )
    password_input = WebDriverWait(driver, timeout_s).until(
        EC.presence_of_element_located((By.ID, "password"))
    )
    email_input.clear()
    email_input.send_keys(email)
    password_input.clear()
    password_input.send_keys(password)

    sign_in_btn = WebDriverWait(driver, timeout_s).until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, "button[type='submit']"))
    )
    sign_in_btn.click()

    WebDriverWait(driver, timeout_s).until(lambda d: _is_logged_in_shell_present(d))
    logger.info("linkedin login success")


def login_linkedin_with_storage_state(
    driver: uc.Chrome,
    *,
    storage_state_path: str,
    timeout_s: int = 45,
) -> int:
    """Restore login session by injecting cookies from storage-state JSON.

    This intentionally mirrors existing working services:
    - inject cookies
    - navigate target page
    - do lightweight authwall validation (not strict DOM gating)
    """
    path = Path(storage_state_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Storage JSON not found: {path}")

    storage_data = _load_storage(path)
    injected = _inject_cookies_with_retry(driver, storage_data, attempts=3)
    if injected <= 0:
        raise RuntimeError("No LinkedIn cookies were injected from storage state.")

    # Set a custom page load timeout for resilience
    driver.set_page_load_timeout(60)
    
    max_nav_attempts = 2
    last_err = None
    for attempt in range(1, max_nav_attempts + 1):
        try:
            logger.info("Navigating to LinkedIn feed (attempt %d/%d)...", attempt, max_nav_attempts)
            driver.get("https://www.linkedin.com/feed/")
            _wait_document_ready(driver)
            break
        except TimeoutException as te:
            last_err = te
            logger.warning("Timeout navigating to feed on attempt %d: %s", attempt, te)
            if attempt < max_nav_attempts:
                time.sleep(2)
        except Exception as e:
            last_err = e
            logger.error("Unexpected error navigating to feed on attempt %d: %s", attempt, e)
            if attempt < max_nav_attempts:
                time.sleep(2)
    else:
        # If we exhausted attempts
        raise RuntimeError(f"Failed to navigate to LinkedIn feed after {max_nav_attempts} attempts: {last_err}")

    # Give LinkedIn time to hydrate late widgets/top-nav before any checks.
    time.sleep(2.5)

    current_url = driver.current_url
    if _is_authwall_url(current_url):
        raise RuntimeError(
            f"storage auth redirected to authwall url={current_url!r} title={driver.title!r}"
        )

    if not _is_logged_in_shell_present(driver):
        # Keep this as warning-only to avoid false failures seen in this repo.
        logger.warning(
            "storage auth shell signals weak; continuing url=%s title=%s",
            current_url,
            driver.title,
        )
    logger.info("linkedin session restored via storage state cookies=%s", injected)
    return injected


def detect_floating_messaging_widget(driver: uc.Chrome) -> FloatingMessagingState:
    """Inspect LinkedIn DOM and detect bottom floating messaging widget state."""
    js = """
        const isVisible = (el) => {
            if (!el) return false;
            const r = el.getBoundingClientRect();
            const s = window.getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
        };
        const deepNodes = (root, selector) => {
            const out = [];
            const stack = [root];
            while (stack.length) {
                const node = stack.pop();
                if (!node || !node.querySelectorAll) continue;
                try { out.push(...Array.from(node.querySelectorAll(selector))); } catch (_) {}
                const all = node.querySelectorAll ? node.querySelectorAll('*') : [];
                for (const el of all) if (el && el.shadowRoot) stack.push(el.shadowRoot);
            }
            return out;
        };
        const roots = [document];
        const interop = document.getElementById('interop-outlet');
        if (interop) roots.push(interop);
        if (interop && interop.shadowRoot) roots.push(interop.shadowRoot);

        const selectors = [
            '#msg-overlay',
            '.msg-overlay-container',
            '.msg-overlay-list-bubble',
            'aside.msg-overlay-list-bubble',
            'aside[class*="msg-overlay"]'
        ];

        const nodes = [];
        for (const sel of selectors) {
            for (const root of roots) {
                for (const n of deepNodes(root, sel)) nodes.push({selector: sel, node: n});
            }
        }
        const seen = new WeakSet();
        const uniq = [];
        for (const item of nodes) {
            if (!item || !item.node || seen.has(item.node)) continue;
            seen.add(item.node);
            uniq.push(item);
        }
        const visible = uniq.filter((item) => isVisible(item.node));

        const score = (item) => {
            const el = item.node;
            const r = el.getBoundingClientRect();
            let s = 0;
            if (item.selector === '#msg-overlay') s += 300;
            if (el.id === 'msg-overlay') s += 250;
            if (el.classList && el.classList.contains('msg-overlay-container')) s += 120;
            if (el.classList && el.classList.contains('msg-overlay-list-bubble--is-minimized')) s += 80;
            if (el.querySelector && el.querySelector('#msg-overlay-list-bubble-header__button')) s += 100;
            if (el.querySelector && el.querySelector('.msg-overlay-bubble-header__details')) s += 80;
            if (r.y > (window.innerHeight * 0.55)) s += 60; // prefer bottom dock region
            if (r.x > (window.innerWidth * 0.45)) s += 40;  // prefer right half
            return s;
        };
        visible.sort((a, b) => score(b) - score(a));
        const best = visible.length ? visible[0] : null;
        const bestRect = best ? best.node.getBoundingClientRect() : null;

        const hasBadge = visible.some((item) =>
            !!item.node.querySelector('.notification-badge, .msg-badge, [class*="badge"], [aria-label*="unread"]')
        );

        const expandedPanelVisible = visible.some((item) => {
            const el = item.node;
            // check for the conversations list container
            const list = el.querySelector('.msg-overlay-list-bubble__conversations-list, .msg-conversations-container__convo-item, .msg-overlay-list-bubble__content');
            if (list) {
                const r = list.getBoundingClientRect();
                if (r.height > 50) return true; 
            }
            // fallback: check the header button's aria state
            const btn = el.querySelector('button[id*="header__button"], [class*="header__button"]');
            if (btn && btn.getAttribute('aria-expanded') === 'true') return true;
            
            return false;
        });

        return {
            found: uniq.length > 0,
            visible: visible.length > 0,
            widget_count: visible.length,
            has_badge: hasBadge,
            expanded_panel_visible: expandedPanelVisible,
            selected_selector: best ? best.selector : '',
            selected_rect: bestRect ? {
                x: Math.round(bestRect.x),
                y: Math.round(bestRect.y),
                w: Math.round(bestRect.width),
                h: Math.round(bestRect.height),
            } : null,
            debug_reason: visible.length > 0 ? "widget_visible" : (uniq.length > 0 ? "widget_hidden" : "widget_not_found"),
        };
    """
    payload = driver.execute_script(js) or {}
    return FloatingMessagingState(
        found=bool(payload.get("found")),
        visible=bool(payload.get("visible")),
        widget_count=int(payload.get("widget_count", 0)),
        has_badge=bool(payload.get("has_badge")),
        expanded_panel_visible=bool(payload.get("expanded_panel_visible")),
        debug_reason=str(payload.get("debug_reason", "")),
        selected_selector=str(payload.get("selected_selector", "")),
        selected_rect=payload.get("selected_rect"),
    )


def capture_messaging_widget_marked_screenshot(
    driver: uc.Chrome,
    *,
    output_dir: Path | None = None,
) -> str | None:
    """Capture screenshot with highlight rectangle over detected messaging widget."""
    out_dir = output_dir or (_PROJECT_ROOT / "debug_output")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"floating_messaging_widget_{int(time.time() * 1000)}.png"

    try:
        driver.execute_script(
            """
            const isVisible = (el) => {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                const s = window.getComputedStyle(el);
                return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
            };
            const deepNodes = (root, selector) => {
                const out = [];
                const stack = [root];
                while (stack.length) {
                    const node = stack.pop();
                    if (!node || !node.querySelectorAll) continue;
                    try { out.push(...Array.from(node.querySelectorAll(selector))); } catch (_) {}
                    const all = node.querySelectorAll ? node.querySelectorAll('*') : [];
                    for (const el of all) if (el && el.shadowRoot) stack.push(el.shadowRoot);
                }
                return out;
            };
            const roots = [document];
            const interop = document.getElementById('interop-outlet');
            if (interop) roots.push(interop);
            if (interop && interop.shadowRoot) roots.push(interop.shadowRoot);

            const selectors = [
                '#msg-overlay',
                '.msg-overlay-container',
                '.msg-overlay-list-bubble',
                'aside.msg-overlay-list-bubble',
                'aside[class*="msg-overlay"]',
            ];
            const raw = [];
            for (const sel of selectors) {
                for (const root of roots) raw.push(...deepNodes(root, sel));
            }
            const seen = new WeakSet();
            const candidates = [];
            for (const n of raw) {
                if (!n || seen.has(n) || !isVisible(n)) continue;
                seen.add(n);
                candidates.push(n);
            }

            if (!candidates.length) return false;
            const score = (el) => {
                const r = el.getBoundingClientRect();
                let s = 0;
                if (el.id === 'msg-overlay') s += 300;
                if (el.classList && el.classList.contains('msg-overlay-container')) s += 120;
                if (el.classList && el.classList.contains('msg-overlay-list-bubble--is-minimized')) s += 80;
                if (el.querySelector && el.querySelector('#msg-overlay-list-bubble-header__button')) s += 100;
                if (el.querySelector && el.querySelector('.msg-overlay-bubble-header__details')) s += 80;
                if (r.y > (window.innerHeight * 0.55)) s += 60;
                if (r.x > (window.innerWidth * 0.45)) s += 40;
                return s;
            };
            candidates.sort((a, b) => score(b) - score(a));
            const target = candidates[0];
            const r = target.getBoundingClientRect();
            const stamp = Date.now();

            const box = document.createElement('div');
            box.id = `__msg_widget_marker_${stamp}`;
            box.style.position = 'fixed';
            box.style.left = `${Math.max(0, r.left - 2)}px`;
            box.style.top = `${Math.max(0, r.top - 2)}px`;
            box.style.width = `${Math.max(8, r.width + 4)}px`;
            box.style.height = `${Math.max(8, r.height + 4)}px`;
            box.style.border = '3px solid #00c853';
            box.style.background = 'rgba(0, 200, 83, 0.10)';
            box.style.zIndex = '2147483646';
            box.style.pointerEvents = 'none';
            box.style.boxSizing = 'border-box';

            const label = document.createElement('div');
            label.id = `__msg_widget_label_${stamp}`;
            label.textContent = 'FloatingMessagingWidget detected';
            label.style.position = 'fixed';
            label.style.left = `${Math.max(0, r.left)}px`;
            label.style.top = `${Math.max(0, r.top - 24)}px`;
            label.style.padding = '3px 6px';
            label.style.background = '#00c853';
            label.style.color = '#fff';
            label.style.font = '700 11px/1.2 Arial, sans-serif';
            label.style.zIndex = '2147483647';
            label.style.pointerEvents = 'none';

            document.body.appendChild(box);
            document.body.appendChild(label);
            return true;
            """
        )
        driver.save_screenshot(str(out_path))
        return str(out_path)
    except Exception as exc:
        logger.warning("failed to capture marked messaging widget screenshot err=%s", exc)
        return None
    finally:
        try:
            driver.execute_script(
                """
                for (const n of Array.from(document.querySelectorAll("[id^='__msg_widget_marker_'], [id^='__msg_widget_label_']"))) {
                    n.remove();
                }
                """
            )
        except Exception:
            pass


def click_floating_messaging_widget(driver: uc.Chrome) -> FloatingMessagingClickResult:
    """Click the floating messaging widget using robust fallback strategies."""
    js = """
        const isVisible = (el) => {
            if (!el) return false;
            const r = el.getBoundingClientRect();
            const s = window.getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
        };
        const deepNodes = (root, selector) => {
            const out = [];
            const stack = [root];
            while (stack.length) {
                const node = stack.pop();
                if (!node || !node.querySelectorAll) continue;
                try { out.push(...Array.from(node.querySelectorAll(selector))); } catch (_) {}
                const all = node.querySelectorAll ? node.querySelectorAll('*') : [];
                for (const el of all) if (el && el.shadowRoot) stack.push(el.shadowRoot);
            }
            return out;
        };
        const roots = [document];
        const interop = document.getElementById('interop-outlet');
        if (interop) roots.push(interop);
        if (interop && interop.shadowRoot) roots.push(interop.shadowRoot);

        const selectors = [
            '#msg-overlay',
            '.msg-overlay-container',
            '.msg-overlay-list-bubble',
            'aside.msg-overlay-list-bubble',
            'aside[class*="msg-overlay"]',
        ];
        const candidates = [];
        const seen = new WeakSet();
        for (const sel of selectors) {
            for (const root of roots) {
                for (const n of deepNodes(root, sel)) {
                    if (!n || seen.has(n) || !isVisible(n)) continue;
                    seen.add(n);
                    candidates.push(n);
                }
            }
        }
        if (!candidates.length) {
            return {clicked:false, click_strategy:'none', open_state_after_click:false, debug_reason:'widget_not_found'};
        }

        const score = (el) => {
            const r = el.getBoundingClientRect();
            let s = 0;
            if (el.id === 'msg-overlay') s += 300;
            if (el.classList && el.classList.contains('msg-overlay-container')) s += 120;
            if (el.classList && el.classList.contains('msg-overlay-list-bubble--is-minimized')) s += 80;
            if (el.querySelector && el.querySelector('#msg-overlay-list-bubble-header__button')) s += 120;
            if (el.querySelector && el.querySelector('.msg-overlay-bubble-header__details')) s += 80;
            if (r.y > (window.innerHeight * 0.55)) s += 60;
            if (r.x > (window.innerWidth * 0.45)) s += 40;
            return s;
        };
        candidates.sort((a, b) => score(b) - score(a));
        const target = candidates[0];
        const headerBtn = target.querySelector('#msg-overlay-list-bubble-header__button') ||
                          target.querySelector('.msg-overlay-bubble-header__button') ||
                          target.querySelector("button[aria-label*='messaging' i]") ||
                          target.querySelector("button");

        const openState = () => {
            const roots2 = [document];
            const interop2 = document.getElementById('interop-outlet');
            if (interop2) roots2.push(interop2);
            if (interop2 && interop2.shadowRoot) roots2.push(interop2.shadowRoot);
            const selectors = [
                '.msg-conversations-container__convo-item',
                '.msg-conversation-listitem',
                '.msg-thread',
                '.msg-s-message-list-content',
                '.msg-form'
            ];
            for (const root of roots2) {
                for (const sel of selectors) {
                    const nodes = deepNodes(root, sel).filter(isVisible);
                    if (nodes.length) return true;
                }
            }
            const bubble = target.querySelector('.msg-overlay-list-bubble') || target;
            const cls = bubble.classList || target.classList;
            if (cls && cls.contains('msg-overlay-list-bubble--is-minimized')) return false;
            return false;
        };

        const clickElement = (el) => {
            if (!el) return false;
            try {
                el.scrollIntoView({block:'end', inline:'nearest'});
                el.focus();
            } catch (_) {}
            try {
                el.click();
                return true;
            } catch (_) {}
            try {
                const r = el.getBoundingClientRect();
                const cx = Math.floor(r.left + r.width / 2);
                const cy = Math.floor(r.top + r.height / 2);
                const events = ['pointerover','mouseover','pointerenter','mouseenter','pointermove','mousemove','pointerdown','mousedown','pointerup','mouseup','click'];
                for (const ev of events) {
                    const Evt = ev.startsWith('pointer') ? PointerEvent : MouseEvent;
                    el.dispatchEvent(new Evt(ev, {bubbles:true, cancelable:true, composed:true, clientX:cx, clientY:cy, pointerType:'mouse'}));
                }
                return true;
            } catch (_) {}
            return false;
        };

        let strategy = 'none';
        let clicked = false;
        if (headerBtn && clickElement(headerBtn)) {
            strategy = 'header_button_click';
            clicked = true;
        } else if (clickElement(target)) {
            strategy = 'container_click';
            clicked = true;
        }

        return {
            clicked,
            click_strategy: strategy,
            open_state_after_click: clicked ? openState() : false,
            debug_reason: clicked ? 'clicked' : 'click_failed',
        };
    """
    payload = driver.execute_script(js) or {}
    return FloatingMessagingClickResult(
        clicked=bool(payload.get("clicked")),
        click_strategy=str(payload.get("click_strategy", "")),
        open_state_after_click=bool(payload.get("open_state_after_click")),
        debug_reason=str(payload.get("debug_reason", "")),
    )


def click_conversation_by_name(driver: uc.Chrome, profile_name: str) -> bool:
    """Find and click a specific conversation card in the messaging overlay by name."""
    js_click = """
    const profileName = (arguments[0] || '').trim();
    const normalize = (s) =>
        (s || '')
            .toLowerCase()
            .replace(/\\s+/g, ' ')
            .replace(/[^a-z0-9 ]/g, '')
            .trim();
    const expectedFull = normalize(profileName);
    const expectedFirst = normalize(profileName.split(' ')[0] || '');
    if (!expectedFull && !expectedFirst) return false;

    const isVisible = (el) => {
        if (!el) return false;
        const r = el.getBoundingClientRect();
        const s = window.getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
    };
    const deepNodes = (root, selector) => {
        const out = [];
        const stack = [root];
        while (stack.length) {
            const node = stack.pop();
            if (!node || !node.querySelectorAll) continue;
            try { out.push(...Array.from(node.querySelectorAll(selector))); } catch (_) {}
            const all = node.querySelectorAll ? node.querySelectorAll('*') : [];
            for (const el of all) if (el && el.shadowRoot) stack.push(el.shadowRoot);
        }
        return out;
    };
    const roots = [document];
    const interop = document.getElementById('interop-outlet');
    if (interop) roots.push(interop);
    if (interop && interop.shadowRoot) roots.push(interop.shadowRoot);

    const rowSelectors = [
        '.msg-conversations-container__convo-item',
        '.msg-conversation-listitem',
        '.msg-conversation-card',
        '[class*="convo-item"]',
        '[class*="convo-card"]',
        '[data-control-name*="conversation"]'
    ];
    const rows = [];
    const seen = new WeakSet();
    for (const root of roots) {
        for (const sel of rowSelectors) {
            for (const node of deepNodes(root, sel)) {
                if (!node || seen.has(node) || !isVisible(node)) continue;
                seen.add(node);
                rows.push(node);
            }
        }
    }

    const isDisallowedTarget = (el) => {
        if (!el) return false;
        return !!el.closest(
            [
                // "More" / ellipsis actions
                '[aria-label*="more" i]',
                '[aria-label*="ellipsis" i]',
                '[data-control-name*="more" i]',
                '[class*="ellipsis"]',
                // Profile/avatar/name links should not be clicked for thread-open action
                'a[href*="/in/"]',
                '[data-control-name*="view_profile" i]',
                '[class*="avatar"]',
                '.presence-entity',
            ].join(',')
        );
    };

    const clickableFor = (row) => {
        // Prefer explicit thread link if present, but never profile/ellipsis controls.
        const primary =
            row.querySelector('a[href*="/messaging/thread/"]') ||
            row.querySelector('[data-control-name*="open_conversation" i]') ||
            row.querySelector('[role="button"]');
        if (primary && !isDisallowedTarget(primary)) return primary;
        return row;
    };

    const clickElement = (el, row) => {
        if (!el) return false;
        try { el.scrollIntoView({ block: 'center', inline: 'nearest' }); } catch (_) {}
        try { el.focus(); } catch (_) {}
        if (!isDisallowedTarget(el)) {
            try { el.click(); return true; } catch (_) {}
        }
        try {
            // Click neutral zone on row body (avoids avatar/name on left and ellipsis on right).
            const base = row || el;
            const r = base.getBoundingClientRect();
            const cx = Math.floor(r.left + (r.width * 0.42));
            const cy = Math.floor(r.top + r.height / 2);
            let tgt = document.elementFromPoint(cx, cy) || base;
            if (isDisallowedTarget(tgt)) {
                const cx2 = Math.floor(r.left + (r.width * 0.58));
                tgt = document.elementFromPoint(cx2, cy) || base;
            }
            if (isDisallowedTarget(tgt)) tgt = base;
            const events = ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'];
            for (const ev of events) {
                const Evt = ev.startsWith('pointer') ? PointerEvent : MouseEvent;
                tgt.dispatchEvent(new Evt(ev, { bubbles: true, cancelable: true, composed: true, clientX: cx, clientY: cy, pointerType: 'mouse' }));
            }
            return true;
        } catch (_) {}
        return false;
    };

    const scoreRow = (row) => {
        const text = normalize(row.innerText || '');
        if (!text) return -1;
        let score = 0;
        if (expectedFull && text.includes(expectedFull)) score += 100;
        if (expectedFirst && text.includes(expectedFirst)) score += 40;
        const rowClass = ((row.className || '') + ' ' + (row.getAttribute('aria-label') || '')).toLowerCase();
        if (rowClass.includes('unread') || rowClass.includes('new')) score += 15;
        if (row.querySelector('.notification-badge, [class*="unread"], [aria-label*="unread" i]')) score += 20;
        return score;
    };

    rows.sort((a, b) => scoreRow(b) - scoreRow(a));
    const best = rows.find((r) => scoreRow(r) > 0);
    if (!best) return false;
    return clickElement(clickableFor(best), best);
    """
    return bool(driver.execute_script(js_click, profile_name))


def extract_messaging_conversations(
    driver: uc.Chrome,
    *,
    max_items: int = 25,
) -> dict[str, Any]:
    """Extract visible conversation rows and unread heuristics from messaging UI."""
    js = """
        const maxItems = Math.max(1, Math.min(100, Number(arguments[0] || 25)));
        const isVisible = (el) => {
            if (!el) return false;
            const r = el.getBoundingClientRect();
            const s = window.getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
        };
        const textOf = (el) => (el && (el.innerText || el.textContent) || '').replace(/\\s+/g, ' ').trim();
        const deepNodes = (root, selector) => {
            const out = [];
            const stack = [root];
            while (stack.length) {
                const node = stack.pop();
                if (!node || !node.querySelectorAll) continue;
                try { out.push(...Array.from(node.querySelectorAll(selector))); } catch (_) {}
                const all = node.querySelectorAll ? node.querySelectorAll('*') : [];
                for (const el of all) if (el && el.shadowRoot) stack.push(el.shadowRoot);
            }
            return out;
        };
        const roots = [document];
        const interop = document.getElementById('interop-outlet');
        if (interop) roots.push(interop);
        if (interop && interop.shadowRoot) roots.push(interop.shadowRoot);

        const overlayCandidates = [];
        const seenOverlay = new WeakSet();
        for (const root of roots) {
            for (const n of deepNodes(root, '#msg-overlay, .msg-overlay-container, aside[class*="msg-overlay"]')) {
                if (!n || seenOverlay.has(n) || !isVisible(n)) continue;
                seenOverlay.add(n);
                overlayCandidates.push(n);
            }
        }

        const rowSelectors = [
            '.msg-conversations-container__convo-item',
            'li.msg-conversations-container__convo-item',
            '.msg-conversation-listitem',
            '.msg-conversation-card',
            '.msg-conversation-listitem--unread',
            '[data-control-name*="conversation"]'
        ];
        const rows = [];
        const seenRows = new WeakSet();
        // Pass 1: rows under visible overlays.
        for (const overlay of overlayCandidates) {
            for (const sel of rowSelectors) {
                for (const n of overlay.querySelectorAll(sel)) {
                    if (!n || seenRows.has(n) || !isVisible(n)) continue;
                    seenRows.add(n);
                    rows.push(n);
                }
            }
        }
        // Pass 2 fallback: global deep search (some LinkedIn variants render list outside picked overlay node).
        if (!rows.length) {
            for (const root of roots) {
                for (const sel of rowSelectors) {
                    for (const n of deepNodes(root, sel)) {
                        if (!n || seenRows.has(n) || !isVisible(n)) continue;
                        seenRows.add(n);
                        rows.push(n);
                    }
                }
            }
        }

        const items = [];
        for (const row of rows) {
            const nameEl =
                row.querySelector('.msg-conversation-listitem__participant-names') ||
                row.querySelector('.msg-conversation-card__participant-names') ||
                row.querySelector('.msg-conversation-listitem__name') ||
                row.querySelector('h3') ||
                row.querySelector('strong');
            const snippetEl =
                row.querySelector('.msg-conversation-listitem__summary') ||
                row.querySelector('.msg-conversation-card__message-snippet') ||
                row.querySelector('.msg-conversation-listitem__message-snippet') ||
                row.querySelector('p');
            const timeEl =
                row.querySelector('time') ||
                row.querySelector('.msg-conversation-listitem__time-stamp') ||
                row.querySelector('.msg-conversation-card__time-stamp');
            const linkEl = row.querySelector("a[href*='/messaging/thread/'], a[href*='/in/']");

            const rowClass = (row.className || '').toLowerCase();
            const badgeUnread = !!row.querySelector(
                '.notification-badge, .msg-conversation-listitem__unread-count, [aria-label*="unread" i], [class*="unread"]'
            );
            const styleUnread = [nameEl, snippetEl].some((el) => {
                if (!el) return false;
                const st = window.getComputedStyle(el);
                const fw = parseInt(st.fontWeight || '400', 10);
                return fw >= 600 || (el.className || '').toLowerCase().includes('t-bold');
            });
            const classUnread = rowClass.includes('unread') || rowClass.includes('new');
            const unread = badgeUnread || classUnread || styleUnread;
            const unreadReason =
                badgeUnread ? 'badge' : (classUnread ? 'row_class' : (styleUnread ? 'bold_text' : ''));

            const profileName = textOf(nameEl);
            const snippet = textOf(snippetEl);
            const timestampText = textOf(timeEl);
            const profileUrl = linkEl ? (linkEl.getAttribute('href') || '') : '';
            const normalizedProfileUrl =
                profileUrl && profileUrl.startsWith('/') ? `https://www.linkedin.com${profileUrl}` : profileUrl;

            if (!profileName && !snippet) continue;
            items.push({
                profile_name: profileName,
                profile_url: normalizedProfileUrl,
                snippet,
                timestamp_text: timestampText,
                unread,
                unread_reason: unreadReason,
            });
            if (items.length >= maxItems) break;
        }

        const unreadCount = items.filter((x) => !!x.unread).length;
        return {
            found: overlayCandidates.length > 0 || items.length > 0,
            count: items.length,
            unread_count: unreadCount,
            items,
            debug_reason: items.length ? 'ok' : (overlayCandidates.length ? 'overlay_found_no_rows' : 'overlay_not_found'),
        };
    """
    payload = driver.execute_script(js, max_items) or {}
    raw_items = payload.get("items") or []
    conversations: list[dict[str, Any]] = []
    for item in raw_items:
        snapshot = MessagingConversationSnapshot(
            profile_name=str(item.get("profile_name", "")),
            profile_url=str(item.get("profile_url", "")),
            snippet=str(item.get("snippet", "")),
            timestamp_text=str(item.get("timestamp_text", "")),
            unread=bool(item.get("unread")),
            unread_reason=str(item.get("unread_reason", "")),
        )
        conversations.append(asdict(snapshot))
    return {
        "found": bool(payload.get("found")),
        "count": int(payload.get("count", 0)),
        "unread_count": int(payload.get("unread_count", 0)),
        "items": conversations,
        "debug_reason": str(payload.get("debug_reason", "")),
    }


def extract_active_thread_messages(driver: uc.Chrome) -> list[dict[str, Any]]:
    """Extract full message history from the currently open/active thread modal, including dates."""
    js = """
        const isVisible = (el) => {
            if (!el) return false;
            const r = el.getBoundingClientRect();
            const s = window.getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
        };
        const textOf = (el) => (el && (el.innerText || el.textContent) || '').replace(/\\s+/g, ' ').trim();
        const deepNodes = (root, selector) => {
            const out = [];
            const stack = [root];
            while (stack.length) {
                const node = stack.pop();
                if (!node || !node.querySelectorAll) continue;
                try { out.push(...Array.from(node.querySelectorAll(selector))); } catch (_) {}
                const all = node.querySelectorAll ? node.querySelectorAll('*') : [];
                for (const el of all) if (el && el.shadowRoot) stack.push(el.shadowRoot);
            }
            return out;
        };
        const roots = [document];
        const interop = document.getElementById('interop-outlet');
        if (interop) roots.push(interop);
        if (interop && interop.shadowRoot) roots.push(interop.shadowRoot);

        // Find the expanded message thread container
        const threadSelectors = [
            '.msg-s-message-list-content',
            '.msg-thread',
            '.msg-s-message-list-container',
            '[class*="message-list"]'
        ];
        let threadContainer = null;
        for (const root of roots) {
            for (const sel of threadSelectors) {
                const found = deepNodes(root, sel).find(isVisible);
                if (found) {
                    threadContainer = found;
                    break;
                }
            }
            if (threadContainer) break;
        }

        if (!threadContainer) return [];

        const history = [];
        let currentDate = "Today"; 
        
        // Find all headings and message items in document order
        const allItems = Array.from(threadContainer.querySelectorAll(
            '.msg-s-message-list__time-heading, [class*="time-heading"], .msg-s-event-listitem, .msg-s-message-group, .msg-s-message-list__event'
        ));
        
        for (const item of allItems) {
            // Check if this item itself is a heading
            if (item.classList.contains('msg-s-message-list__time-heading') || item.matches('[class*="time-heading"]')) {
                currentDate = textOf(item);
                continue;
            }
            
            // Or if it contains a heading (sometimes the LI contains the heading)
            const innerHeading = item.querySelector('.msg-s-message-list__time-heading, [class*="time-heading"]');
            if (innerHeading && !item.querySelector('.msg-s-event-listitem__body, .msg-s-message-group__content')) {
                currentDate = textOf(innerHeading);
                continue;
            }

            // Extract message data if it's a message event
            const nameEl = item.querySelector('.msg-s-message-group__name, .msg-s-event-listitem__name, h3, cite');
            const timeEl = item.querySelector('time, .msg-s-event-listitem__time-stamp');
            const bodyEl = item.querySelector('.msg-s-event-listitem__body, .msg-s-message-group__content, p, [class*="body"]');
            
            const sender = textOf(nameEl);
            const msgTime = textOf(timeEl);
            const content = textOf(bodyEl);
            
            if (!content && !sender) continue;

            const isMe = item.classList.contains('msg-s-event-listitem--outbound') || 
                         item.innerHTML.includes('msg-s-event-listitem--outbound') ||
                         (item.closest && !!item.closest('.msg-s-event-listitem--outbound')) ||
                         (sender.toLowerCase().includes('you'));

            history.push({
                sender: sender || (isMe ? 'Me' : 'Other'),
                date_heading: currentDate,
                time: msgTime,
                full_timestamp: (currentDate + " " + msgTime).trim(),
                content: content,
                direction: isMe ? 'outbound' : 'inbound'
            });
        }
        return history;
    """
    return driver.execute_script(js) or []


def extract_messaging_conversations_with_retry(
    driver: uc.Chrome,
    *,
    max_items: int = 25,
    attempts: int = 6,
    interval_s: float = 1.0,
) -> dict[str, Any]:
    """Poll conversation extraction to handle delayed overlay hydration."""
    last: dict[str, Any] = {}
    total = max(1, attempts)
    for idx in range(total):
        snapshot = extract_messaging_conversations(driver, max_items=max_items)
        last = snapshot
        if int(snapshot.get("count", 0)) > 0:
            snapshot["attempts_used"] = idx + 1
            return snapshot
        if idx < total - 1:
            time.sleep(max(0.1, interval_s))
    last["attempts_used"] = total
    return last


def bootstrap_inbox_scraper(config: InboxScraperConfig | None = None) -> dict[str, Any]:
    """Run the minimal bootstrap flow and return a structured result."""
    cfg = config or InboxScraperConfig()
    driver = _build_driver(headless=cfg.headless)
    try:
        auth_mode = "storage_state"
        cookie_count = 0
        try:
            cookie_count = login_linkedin_with_storage_state(
                driver,
                storage_state_path=cfg.storage_state_path,
                timeout_s=cfg.login_timeout_s,
            )
        except Exception as exc:
            logger.warning("storage-state login failed; checking credentials fallback err=%s", exc)
            email = os.environ.get("LINKEDIN_EMAIL", "").strip()
            password = os.environ.get("LINKEDIN_PASSWORD", "").strip()
            if email and password:
                login_linkedin_with_credentials(
                    driver,
                    email=email,
                    password=password,
                    timeout_s=cfg.login_timeout_s,
                )
                auth_mode = "credentials_fallback"
            else:
                raise RuntimeError(
                    "Storage-session login failed and credential fallback is not configured. "
                    "Set LINKEDIN_EMAIL/LINKEDIN_PASSWORD only if you want fallback."
                ) from exc

        driver.get(cfg.linkedin_home_url)
        _wait_document_ready(driver)
        time.sleep(max(0.0, cfg.post_login_wait_s))

        # ... (previous code above)
        
        # We start the loop or return one-shot result
        if getattr(cfg, "watcher_mode", False):
            run_inbox_watcher(driver, cfg)
            return {"ok": True, "mode": "watcher_completed"}
        else:
            return process_inbox_turn(driver, cfg, auth_mode, cookie_count)

    finally:
        if cfg.wait_before_close_s > 0:
            time.sleep(cfg.wait_before_close_s)
        driver.quit()

def process_inbox_turn(driver: uc.Chrome, cfg: InboxScraperConfig, auth_mode: str, cookie_count: int) -> dict[str, Any]:
    """
    Perform a single scan of the inbox, resolve profiles, and update DB.
    """
    messaging_state = detect_floating_messaging_widget(driver)
    
    if messaging_state.visible and not messaging_state.expanded_panel_visible:
        logger.info("Messaging widget visible but collapsed. Clicking to expand...")
        click_result = click_floating_messaging_widget(driver)
    elif messaging_state.visible and messaging_state.expanded_panel_visible:
        logger.info("Messaging widget already expanded. Skipping click to maintain open state.")
        click_result = FloatingMessagingClickResult(
            clicked=False,
            click_strategy="skipped_already_expanded",
            open_state_after_click=True,
            debug_reason="already_expanded",
        )
    else:
        logger.warning("Messaging widget not visible or found.")
        click_result = FloatingMessagingClickResult(
            clicked=False,
            click_strategy="skipped_widget_not_visible",
            open_state_after_click=False,
            debug_reason="widget_not_visible",
        )
    time.sleep(1.2)
    conversation_snapshot = extract_messaging_conversations_with_retry(
        driver,
        max_items=25,
        attempts=6,
        interval_s=1.0,
    )
    
    for convo in conversation_snapshot.get("items", []):
        profile_name = str(convo.get("profile_name") or "")
        profile_url = str(convo.get("profile_url") or "")
        snippet = str(convo.get("snippet") or "")
        unread = bool(convo.get("unread"))
        
        # Deep Resolve & DB Persistence
        actual_profile_url = profile_url
        if unread:
            logger.info(f"Unread message from {profile_name}. Triggering Deep Resolve...")
            
            deep_url = resolve_recruiter_profile_url(
                driver,
                lambda: click_conversation_by_name(driver, profile_name),
                expected_profile_name=profile_name,
            )
            if deep_url:
                actual_profile_url = deep_url
                logger.info(
                    "Deep Resolve success profile_name=%s resolved_profile_url=%s",
                    profile_name,
                    actual_profile_url,
                )
            else:
                logger.info(
                    "Deep Resolve no-url profile_name=%s fallback_profile_url=%s",
                    profile_name,
                    actual_profile_url,
                )
        
        from services.linkedin_recruiter.message_sender import _find_message_composer_webelement, _close_message_modal_after_send, _fill_and_send_message
        from services.linkedin_recruiter.connection_request_sender import _human_like_click

        # 2. Click the composer to fully hydrate UI for reading/typing
        composer = _find_message_composer_webelement(driver)
        if composer:
            logger.info("Focusing message composer native text area.")
            _human_like_click(driver, composer, label="inbox_focus_composer")
            time.sleep(1.2)

        # --- Only process unread messages for DB storage & pipeline ---
        if not unread:
            logger.debug("Skipping read message from %s", profile_name)
            continue

        recruiter_id = get_recruiter_id_by_linkedin_url(actual_profile_url)
        convo_id = None
        msg_owner_type = OwnerType.RECRUITER_CONVERSATION
        msg_owner_id = None

        if recruiter_id:
            logger.info(
                "Recruiter match found profile_url=%s recruiter_id=%s",
                actual_profile_url,
                recruiter_id,
            )
            db_convo = get_conversation_by_recruiter_id(recruiter_id)
            if not db_convo:
                db_convo = RecruiterConversation(
                    recruiter_id=recruiter_id,
                    channel="linkedin",
                    conversation_context_json={
                        "profile_name": profile_name,
                        "resolved_profile_url": actual_profile_url,
                    },
                )
            db_convo.last_message_at = datetime.now()
            convo_id = upsert_conversation(db_convo)
            msg_owner_type = OwnerType.RECRUITER_CONVERSATION
            msg_owner_id = convo_id
            logger.info("Conversation upserted recruiter_id=%s conversation_id=%s", recruiter_id, convo_id)
        else:
            # Not in lusha_recruiters — store as a LinkedIn PM sender
            logger.info("No recruiter match profile_url=%s — storing as linkedin_pm_sender", actual_profile_url)
            sender_id = upsert_linkedin_pm_sender(
                sender_name=profile_name,
                linkedin_profile_url=actual_profile_url or None,
            )
            msg_owner_type = OwnerType.LINKEDIN_SENDER
            msg_owner_id = sender_id
            convo_id = None
            logger.info("PM sender upserted sender_id=%s (no recruiter_conversation created)", sender_id)

        # --- Full History Synchronization ---
        history = extract_active_thread_messages(driver)
        logger.info("Thread history extracted for %s: %d items", profile_name, len(history))

        # Fetch existing messages to avoid duplicates
        existing_msgs: list[ConversationMessage] = []
        if convo_id:
            existing_msgs = get_messages_by_conversation(convo_id)
        
        new_messages_count = 0
        for h_msg in history:
            def timestamps_match(db_ts, fresh_ts):
                if not db_ts or not fresh_ts: return False
                if db_ts == fresh_ts: return True
                # Normalize "NEW" markers (e.g. "TODAY NEW" vs "TODAY 5:28 PM")
                db_norm = db_ts.replace("NEW", "").strip()
                fresh_norm = fresh_ts.replace("NEW", "").strip()
                # If they share the same date prefix and content/direction match, it's a match
                return db_norm == fresh_norm or db_norm in fresh_norm or fresh_norm in db_norm

            # Duplicate detection: match on content, direction and normalized full_timestamp
            is_dup = any(
                m.content_text == h_msg["content"] and 
                m.direction == h_msg["direction"] and
                timestamps_match((m.message_context_json or {}).get("full_timestamp"), h_msg["full_timestamp"])
                for m in existing_msgs
            )
            
            if not is_dup:
                # Map direction to sender_type
                # Inbound -> recruiter, Outbound -> bot (default for bot-managed flow)
                s_type = "recruiter" if h_msg["direction"] == "inbound" else "bot"
                
                new_msg = ConversationMessage(
                    conversation_id=convo_id,
                    owner_type=msg_owner_type,
                    owner_id=msg_owner_id,
                    sender_type=s_type,
                    direction=h_msg["direction"],
                    content_text=h_msg["content"],
                    context_source="linkedin_inbox.inbox_scraper.sync",
                    message_context_json={
                        "unread": unread,
                        "owner_type": msg_owner_type.value,
                        "resolved_url": actual_profile_url,
                        "full_timestamp": h_msg["full_timestamp"],
                        "date_heading": h_msg["date_heading"],
                        "time": h_msg["time"],
                    }
                )
                save_message(new_msg)
                new_messages_count += 1
                # Update existing_msgs so we don't save the same history item twice if it repeats in the same list
                existing_msgs.append(new_msg)

        logger.info(
            "Sync complete for %s. New messages saved: %d",
            profile_name,
            new_messages_count
        )
        if msg_owner_type == OwnerType.RECRUITER_CONVERSATION:
            intent_obj = process_latest_message_intent(convo_id, snippet)
            logger.info("Intent processing completed conversation_id=%s intent=%s", convo_id, getattr(intent_obj, "value", intent_obj))
            
            if intent_obj and intent_obj.value in ("neutral", "positive_clarification"):
                from services.response_generator.response_service import draft_response
                logger.info("Conversation %s requires a drafted reply. Triggering response generator...", convo_id)
                drafted_msg_id = draft_response(convo_id)
                if drafted_msg_id:
                    logger.info("Successfully drafted AI reply message_id=%s based on intent=%s", drafted_msg_id, intent_obj.value)
                    
                    # Fetch conversation messages to find the newly drafted one
                    all_convo_msgs = get_messages_by_conversation(convo_id)
                    drafted_msg = next((m for m in all_convo_msgs if m.id == drafted_msg_id), None)
                    
                    if drafted_msg and drafted_msg.content_text:
                        logger.info("Auto-typing AI drafted reply natively into LinkedIn UI...")
                        sent_ok, _ = _fill_and_send_message(driver, drafted_msg.content_text)
                        if sent_ok:
                            logger.info("Successfully transmitted mapped response for convo %s", convo_id)
                            update_message_delivery_status(drafted_msg_id, DeliveryStatus.SENT)
                        else:
                            logger.error("Failed native message UI transmission for convo %s", convo_id)

        # Always close the active message modal at the end of its turn to clean workspace state
        logger.info("Tearing down message modal post-turn.")
        _close_message_modal_after_send(driver)

    screenshot_path = capture_messaging_widget_marked_screenshot(driver)
    return {
        "ok": True,
        "auth_mode": auth_mode,
        "cookie_count_injected": cookie_count,
        "floating_messaging_state": asdict(messaging_state),
        "messaging_conversations": conversation_snapshot,
        "floating_messaging_marked_screenshot": screenshot_path,
    }

def run_inbox_watcher(driver: uc.Chrome, cfg: InboxScraperConfig):
    """
    Persistent loop to watch for unread messages.
    """
    interval = getattr(cfg, "watch_interval_s", 60)
    duration_mins = int(os.environ.get("INBOX_WATCHER_DURATION_MINS", "20"))
    end_time = time.time() + (duration_mins * 60)
    logger.info(f"InboxWatcher: Starting persistent loop with {interval}s interval for {duration_mins} minutes.")
    
    while True:
        if time.time() > end_time:
            logger.info("InboxWatcher: Duration limit reached. Exiting.")
            break
            
        try:
            logger.info("InboxWatcher: Checking for updates...")
            # Ensure we are on a page where the messaging widget is visible
            if "linkedin.com" not in driver.current_url:
                driver.get(cfg.linkedin_home_url)
                time.sleep(4)
            
            process_inbox_turn(driver, cfg, "watcher", 0)
            
            logger.info(f"InboxWatcher: Turn complete. Sleeping for {interval}s.")
            time.sleep(interval)
            
        except KeyboardInterrupt:
            logger.info("InboxWatcher: Interrupted by user. Exiting.")
            break
        except Exception as exc:
            logger.exception(f"InboxWatcher: Error in loop: {exc}")
            time.sleep(interval)

