"""LinkedIn connection request sender using undetected-chromedriver."""

from __future__ import annotations

import logging
import os
import random
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import WebDriverException
import undetected_chromedriver as uc

from services.linkedin_recruiter.ellipsis_menu_service import (
    DEFAULT_STORAGE_PATH,
    _inject_linkedin_cookies,
    _load_storage,
)

logger = logging.getLogger(__name__)


def _is_local_webdriver_timeout_error(exc: Exception) -> bool:
    """True when exception chain indicates WebDriver localhost read timeout."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        msg = str(current)
        if (
            "HTTPConnectionPool(host='localhost'" in msg
            and "Read timed out" in msg
        ):
            return True
        current = current.__cause__ or current.__context__
    return False


def _project_root_dir() -> Path:
    return Path(__file__).resolve().parents[2]


def _capture_pre_send_click_debug_screenshot(driver: uc.Chrome) -> str | None:
    """Capture screenshot with Send button highlighted before click."""
    try:
        marker = driver.execute_script(
            """
            const isVisible = (el) => {
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
            const dialogs = roots.flatMap((r) => deepNodes(r, "div[role='dialog']")).filter(isVisible);
            const sendNode = dialogs.flatMap((d) => Array.from(d.querySelectorAll("button"))).find((btn) => {
                if (!isVisible(btn) || btn.disabled) return false;
                const aria = (btn.getAttribute('aria-label') || '').trim().toLowerCase();
                const txt = (btn.innerText || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                const hasArtdecoSendSpan = !!btn.querySelector("span.artdeco-button__text");
                return (
                    aria === 'send invitation' ||
                    (aria.includes('send invitation') && btn.className.toLowerCase().includes('artdeco-button')) ||
                    (hasArtdecoSendSpan && txt === 'send') ||
                    txt === 'send'
                );
            });
            if (!sendNode) return null;

            const r = sendNode.getBoundingClientRect();
            const mark = document.createElement('div');
            const label = document.createElement('div');
            const id = '__send_click_marker_' + Date.now();
            mark.id = id;
            mark.style.position = 'fixed';
            mark.style.left = `${Math.max(0, r.left - 3)}px`;
            mark.style.top = `${Math.max(0, r.top - 3)}px`;
            mark.style.width = `${Math.max(8, r.width + 6)}px`;
            mark.style.height = `${Math.max(8, r.height + 6)}px`;
            mark.style.border = '3px solid #ff1744';
            mark.style.background = 'rgba(255, 23, 68, 0.08)';
            mark.style.zIndex = '2147483647';
            mark.style.pointerEvents = 'none';
            mark.style.boxSizing = 'border-box';

            label.textContent = 'SEND TARGET';
            label.style.position = 'fixed';
            label.style.left = `${Math.max(0, r.left)}px`;
            label.style.top = `${Math.max(0, r.top - 24)}px`;
            label.style.padding = '2px 6px';
            label.style.background = '#ff1744';
            label.style.color = '#fff';
            label.style.font = '700 12px/1.2 Arial, sans-serif';
            label.style.zIndex = '2147483647';
            label.style.pointerEvents = 'none';
            label.id = id + '_label';

            document.body.appendChild(mark);
            document.body.appendChild(label);
            return {
                id,
                rect: {
                    x: Math.round(r.x),
                    y: Math.round(r.y),
                    w: Math.round(r.width),
                    h: Math.round(r.height),
                },
            };
            """
        )
        if not marker:
            logger.info("pre-send debug marker could not locate send button")
            return None

        debug_dir = _project_root_dir() / "debug_output"
        debug_dir.mkdir(parents=True, exist_ok=True)
        stamp_ms = int(time.time() * 1000)
        out_path = debug_dir / f"send_preclick_marked_{stamp_ms}.png"
        driver.save_screenshot(str(out_path))
        logger.info("pre-send screenshot saved=%s rect=%s", out_path, marker.get("rect"))
        return str(out_path)
    except Exception as exc:
        logger.info("pre-send screenshot capture failed err=%s", exc)
        return None
    finally:
        try:
            driver.execute_script(
                """
                const marks = Array.from(document.querySelectorAll("[id^='__send_click_marker_']"));
                for (const m of marks) m.remove();
                """
            )
        except Exception:
            pass


def _capture_invitation_modal_snapshot(driver: uc.Chrome) -> dict[str, Any]:
    """Collect visible modal content and clickable candidates for debugging send detection."""
    js = """
        const isVisible = (el) => {
            const r = el.getBoundingClientRect();
            const s = window.getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
        };
        const textOf = (el) => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
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
        const dialogs = roots.flatMap((r) => deepNodes(r, "div[role='dialog']")).filter(isVisible);
        const dialogSummaries = dialogs.map((d, idx) => {
            const rect = d.getBoundingClientRect();
            const nodes = Array.from(
                d.querySelectorAll("button, a[role='button'], div[role='button'], span.artdeco-button__text")
            )
                .filter(isVisible)
                .slice(0, 40)
                .map((n) => {
                    const r = n.getBoundingClientRect();
                    const ownerBtn = n.closest('button, a[role=\"button\"], div[role=\"button\"]');
                    return {
                        tag: n.tagName.toLowerCase(),
                        text: textOf(n).slice(0, 120),
                        aria: (n.getAttribute('aria-label') || '').slice(0, 120),
                        cls: (typeof n.className === 'string' ? n.className : '').slice(0, 160),
                        ownerTag: ownerBtn ? ownerBtn.tagName.toLowerCase() : '',
                        ownerAria: ownerBtn ? ((ownerBtn.getAttribute('aria-label') || '').slice(0, 120)) : '',
                        ownerDisabled: !!(ownerBtn && (ownerBtn.disabled || ownerBtn.getAttribute('aria-disabled') === 'true')),
                        rect: {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)},
                    };
                });
            return {
                index: idx,
                rect: {x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height)},
                text: textOf(d).slice(0, 500),
                clickableNodes: nodes,
            };
        });

        return {
            dialogCountVisible: dialogs.length,
            dialogs: dialogSummaries,
            url: window.location.href,
            title: document.title,
            ts: Date.now(),
        };
    """
    try:
        return driver.execute_script(js) or {}
    except Exception as exc:
        return {"error": str(exc)}


def _click_send_shadow_aware(driver: uc.Chrome) -> bool:
    """Click Send from invitation dialog using shadow-aware traversal."""
    js = """
        const isVisible = (el) => {
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
        for (const root of roots) {
            const dialogs = deepNodes(root, "div[role='dialog']").filter(isVisible);
            for (const dialog of dialogs) {
                const buttons = Array.from(dialog.querySelectorAll("button")).filter(isVisible);
                const sendBtn = buttons.find((b) => {
                    const txt = (b.innerText || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                    const aria = (b.getAttribute('aria-label') || '').trim().toLowerCase();
                    return !b.disabled && (aria === 'send invitation' || txt === 'send');
                });
                if (!sendBtn) continue;
                sendBtn.focus();
                const r = sendBtn.getBoundingClientRect();
                const cx = Math.floor(r.left + (r.width / 2));
                const cy = Math.floor(r.top + (r.height / 2));
                const events = ['pointerover', 'mouseover', 'pointerenter', 'mouseenter', 'pointermove', 'mousemove', 'pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'];
                for (const ev of events) {
                    const Evt = ev.startsWith('pointer') ? PointerEvent : MouseEvent;
                    sendBtn.dispatchEvent(new Evt(ev, {bubbles:true, cancelable:true, composed:true, clientX:cx, clientY:cy, pointerType:'mouse'}));
                }
                try { sendBtn.click(); } catch (_) {}
                return true;
            }
        }
        return false;
    """
    try:
        return bool(driver.execute_script(js))
    except Exception:
        return False


def _is_send_button_visible_shadow_aware(driver: uc.Chrome) -> bool:
    """Return True if a visible, enabled Send button still exists in dialog."""
    js = """
        const isVisible = (el) => {
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
        for (const root of roots) {
            const dialogs = deepNodes(root, "div[role='dialog']").filter(isVisible);
            for (const dialog of dialogs) {
                const buttons = Array.from(dialog.querySelectorAll("button")).filter(isVisible);
                const sendBtn = buttons.find((b) => {
                    const txt = (b.innerText || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                    const aria = (b.getAttribute('aria-label') || '').trim().toLowerCase();
                    return !(b.disabled || b.getAttribute('aria-disabled') === 'true') && (aria === 'send invitation' || txt === 'send');
                });
                if (sendBtn) return true;
            }
        }
        return false;
    """
    try:
        return bool(driver.execute_script(js))
    except Exception:
        return False


def _wait_send_registration(driver: uc.Chrome, timeout_s: float = 2.5) -> bool:
    """Heuristic: send registers when modal send button disappears quickly."""
    deadline = time.time() + max(0.2, timeout_s)
    while time.time() < deadline:
        if not _is_send_button_visible_shadow_aware(driver):
            return True
        time.sleep(0.18)
    return False


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


def _random_note_text(max_len: int = 200) -> str:
    templates = [
        "Hi {name}, I came across your profile and would like to connect regarding opportunities.",
        "Hello {name}, your background stood out to me. I would love to connect and stay in touch.",
        "Hi {name}, I am building my network in this domain and would be glad to connect with you.",
        "Hello {name}, I found your profile relevant and would appreciate connecting with you.",
    ]
    raw_name = random.choice(["there", "friend", ""])
    text = random.choice(templates).format(name=raw_name).replace("  ", " ").strip()
    if len(text) > max_len:
        text = text[:max_len].rstrip()
    return text


def _human_pause(min_s: float, max_s: float, *, label: str) -> None:
    lo = max(0.0, min_s)
    hi = max(lo, max_s)
    delay = random.uniform(lo, hi)
    logger.info("human-like pause label=%s delay=%.2fs", label, delay)
    time.sleep(delay)


def _type_text_human_like_webelement(element: Any, text: str) -> None:
    """Type text char-by-char with slight random delays."""
    for ch in text:
        element.send_keys(ch)
        time.sleep(random.uniform(0.03, 0.11))


def _type_text_human_like_shadow_aware(driver: uc.Chrome, text: str) -> bool:
    """Find visible textarea across shadow roots and type text slowly."""
    try:
        typed = bool(
            driver.execute_async_script(
                """
                const done = arguments[arguments.length - 1];
                const txt = arguments[0];
                const minMs = arguments[1];
                const maxMs = arguments[2];
                const isVisible = (el) => {
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
                const areas = roots.flatMap((r) => deepNodes(r, 'textarea')).filter(isVisible);
                if (!areas.length) { done(false); return; }
                const box = areas[0];
                box.focus();
                box.value = '';
                box.dispatchEvent(new Event('input', {bubbles:true}));

                const rand = (a, b) => Math.floor(a + Math.random() * (b - a + 1));
                let i = 0;
                const tick = () => {
                    if (i >= txt.length) {
                        box.dispatchEvent(new Event('change', {bubbles:true}));
                        done(true);
                        return;
                    }
                    box.value += txt[i];
                    box.dispatchEvent(new Event('input', {bubbles:true}));
                    i += 1;
                    setTimeout(tick, rand(minMs, maxMs));
                };
                tick();
                """,
                text,
                35,
                120,
            )
        )
        return typed
    except Exception:
        return False


def _dispatch_mouse_sequence_js(driver: uc.Chrome, element: Any) -> bool:
    try:
        return bool(
            driver.execute_script(
                """
                const el = arguments[0];
                if (!el) return false;
                const events = ['mouseover', 'mouseenter', 'mousemove', 'mousedown', 'mouseup', 'click'];
                for (const ev of events) {
                    el.dispatchEvent(new MouseEvent(ev, {bubbles:true, cancelable:true, view:window}));
                }
                return true;
                """,
                element,
            )
        )
    except Exception:
        return False


def _human_like_mouse_move(driver: uc.Chrome, element: Any, *, label: str) -> None:
    """Move cursor in a couple of small hops before click."""
    try:
        rect = driver.execute_script(
            "const r=arguments[0].getBoundingClientRect(); return {w:Math.max(1,Math.round(r.width)),h:Math.max(1,Math.round(r.height))};",
            element,
        ) or {"w": 1, "h": 1}
        width = max(1, int(rect.get("w", 1)))
        height = max(1, int(rect.get("h", 1)))

        # Selenium offset is relative to element center, not top-left.
        half_w = max(0, width // 2)
        half_h = max(0, height // 2)
        pad_x = min(4, max(0, width // 4))
        pad_y = min(4, max(0, height // 4))
        left = -max(0, half_w - pad_x)
        top = -max(0, half_h - pad_y)
        right = max(0, half_w - pad_x)
        bottom = max(0, half_h - pad_y)

        x1 = random.randint(left, right)
        y1 = random.randint(top, bottom)
        x2 = random.randint(left, right)
        y2 = random.randint(top, bottom)

        ActionChains(driver).move_to_element_with_offset(element, x1, y1).pause(
            random.uniform(0.08, 0.2)
        ).move_to_element_with_offset(element, x2, y2).pause(random.uniform(0.06, 0.16)).perform()
        logger.info(
            "mouse movement completed label=%s element_wh=(%s,%s) offsets=[(%s,%s),(%s,%s)]",
            label,
            width,
            height,
            x1,
            y1,
            x2,
            y2,
        )
    except Exception as exc:
        logger.info("mouse movement failed label=%s err=%s", label, exc)
        if _is_local_webdriver_timeout_error(exc):
            raise WebDriverException(
                f"Critical WebDriver timeout during mouse move: {label}"
            ) from exc


def _human_like_click(driver: uc.Chrome, element: Any, *, label: str) -> bool:
    """Human-like pre/post movement with stable click dispatch (no forced scrolling)."""
    _human_like_mouse_move(driver, element, label=f"{label}:pre_click_move")
    _human_pause(0.06, 0.2, label=f"{label}:after_mouse_move")

    try:
        element.click()
        logger.info("click success via WebElement.click label=%s", label)
        _human_pause(0.14, 0.42, label=f"{label}:after_click")
        return True
    except Exception as exc:
        logger.info("click WebElement.click failed label=%s err=%s", label, exc)
        if _is_local_webdriver_timeout_error(exc):
            raise WebDriverException(
                f"Critical WebDriver timeout during WebElement.click: {label}"
            ) from exc

    try:
        driver.execute_script("arguments[0].click();", element)
        logger.info("click success via JS element.click label=%s", label)
        _human_pause(0.14, 0.42, label=f"{label}:after_click")
        return True
    except Exception as exc:
        logger.info("click JS element.click failed label=%s err=%s", label, exc)
        if _is_local_webdriver_timeout_error(exc):
            raise WebDriverException(
                f"Critical WebDriver timeout during JS click: {label}"
            ) from exc

    if _dispatch_mouse_sequence_js(driver, element):
        logger.info("click success via JS mouse sequence label=%s", label)
        _human_pause(0.14, 0.42, label=f"{label}:after_click")
        return True
    return False


def _diagnose_connect_candidates(driver: uc.Chrome) -> list[dict[str, Any]]:
    js = """
        const isVisible = (el) => {
            const r = el.getBoundingClientRect();
            const s = window.getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
        };
        const out = [];
        const nodes = Array.from(document.querySelectorAll("button, a[role='button'], div[role='button']"));
        for (const el of nodes) {
            if (!isVisible(el)) continue;
            const text = (el.innerText || '').replace(/\\s+/g, ' ').trim();
            const aria = (el.getAttribute('aria-label') || '').trim();
            const low = (text + ' ' + aria).toLowerCase();
            if (!(low.includes('connect') || low.includes('invite'))) continue;
            const r = el.getBoundingClientRect();
            out.push({
                text,
                aria,
                x: Math.round(r.x),
                y: Math.round(r.y),
                w: Math.round(r.width),
                h: Math.round(r.height),
                inMain: !!el.closest('main'),
                inTopCard: !!el.closest('main section'),
                inRightRail: r.x >= 1000,
                parentClass: el.parentElement ? (el.parentElement.className || '') : '',
            });
            if (out.length >= 25) break;
        }
        return out;
    """
    return driver.execute_script(js) or []


def _get_profile_ellipsis_rect(driver: uc.Chrome) -> dict[str, int] | None:
    selectors = [
        "main button[aria-label*='More']",
        "button[aria-label='More actions']",
        "button[aria-label*='More'][aria-label*='actions']",
    ]
    for selector in selectors:
        elements = driver.find_elements(By.CSS_SELECTOR, selector)
        for el in elements:
            if not el.is_displayed():
                continue
            rect = driver.execute_script(
                "const r=arguments[0].getBoundingClientRect(); return {x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)};",
                el,
            )
            if not rect:
                continue
            # Ignore navbar "More" and focus on profile section.
            if rect["y"] < 120:
                continue
            logger.info("profile ellipsis rect=%s selector=%s", rect, selector)
            return rect
    logger.info("profile ellipsis rect not found")
    return None


def _click_connect_near_ellipsis(driver: uc.Chrome, timeout_s: int) -> tuple[bool, str | None]:
    # Highest priority: the exact profile Connect signature captured from click-recorder.
    # target: span classes inside anchor with aria-label "Invite <name> to connect"
    js_recorded_signature = """
        const isVisible = (el) => {
            const r = el.getBoundingClientRect();
            const s = window.getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
        };
        const moreBtn = Array.from(document.querySelectorAll(
            "main button[aria-label*='More'], button[aria-label='More actions'], button[aria-label*='More'][aria-label*='actions']"
        )).filter(isVisible).find((el) => el.getBoundingClientRect().y >= 120);
        const moreRect = moreBtn ? moreBtn.getBoundingClientRect() : null;

        const anchors = Array.from(document.querySelectorAll("a[aria-label]")).filter(isVisible);
        const candidates = [];
        for (const a of anchors) {
            const aria = (a.getAttribute('aria-label') || '').trim();
            const low = aria.toLowerCase();
            if (!(low.startsWith('invite ') && low.endsWith(' to connect'))) continue;
            const r = a.getBoundingClientRect();
            // Exclude right rail suggestions.
            if (r.x > 1000) continue;
            // If ellipsis is known, prefer same row vicinity.
            if (moreRect && Math.abs(r.y - moreRect.y) > 70) continue;
            const span = a.querySelector('span');
            const spanText = span ? (span.innerText || '').replace(/\\s+/g, ' ').trim() : '';
            candidates.push({a, aria, text: (a.innerText || '').trim(), spanText, x:r.x, y:r.y, w:r.width, h:r.height});
        }

        if (candidates.length === 0) return {clicked:false, reason:'recorded_signature_not_found'};
        candidates.sort((x, y) => (x.y - y.y) || (x.x - y.x));
        const pick = candidates[0];
        window.__lastConnectClickRect = {
            x: Math.round(pick.x), y: Math.round(pick.y), w: Math.round(pick.w), h: Math.round(pick.h)
        };
        pick.a.click();
        return {
            clicked:true,
            reason:'recorded_signature_anchor_invite',
            aria:pick.aria,
            text:pick.text,
            spanText:pick.spanText,
            rect:{x:Math.round(pick.x), y:Math.round(pick.y), w:Math.round(pick.w), h:Math.round(pick.h)},
            candidates:candidates.length
        };
    """
    signature_result = driver.execute_script(js_recorded_signature) or {}
    logger.info("connect recorded-signature result=%s", signature_result)
    if bool(signature_result.get("clicked")):
        return True, "recorded_signature_anchor_invite"

    # First try: profile action toolbar (where Connect is usually visible next to Message/ellipsis).
    js_toolbar_first = """
        const isVisible = (el) => {
            const r = el.getBoundingClientRect();
            const s = window.getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
        };
        const toolbars = Array.from(document.querySelectorAll("main [role='toolbar']")).filter(isVisible);
        if (toolbars.length === 0) return {clicked:false, reason:'toolbar_not_found'};

        const candidates = [];
        for (const bar of toolbars) {
            const barRect = bar.getBoundingClientRect();
            if (barRect.y < 120 || barRect.y > 950) continue;
            const btns = Array.from(bar.querySelectorAll("button, a[role='button']")).filter(isVisible);
            for (const b of btns) {
                const text = (b.innerText || '').replace(/\\s+/g, ' ').trim();
                const aria = (b.getAttribute('aria-label') || '').trim();
                const low = (text + ' ' + aria).toLowerCase();
                if (!(low.includes('connect') || low.includes('invite'))) continue;
                const r = b.getBoundingClientRect();
                if (r.x > 1000) continue; // skip right rail region
                candidates.push({el:b, text, aria, x:r.x, y:r.y, toolbarY:barRect.y});
            }
        }

        if (candidates.length === 0) return {clicked:false, reason:'toolbar_connect_not_found'};
        candidates.sort((a,b) => (a.toolbarY - b.toolbarY) || (a.x - b.x));
        candidates[0].el.click();
        return {
            clicked:true,
            reason:'toolbar_connect',
            text:candidates[0].text,
            aria:candidates[0].aria,
            count:candidates.length
        };
    """
    toolbar_result = driver.execute_script(js_toolbar_first) or {}
    logger.info("connect toolbar-first result=%s", toolbar_result)
    if bool(toolbar_result.get("clicked")):
        return True, "profile_toolbar_connect"

    # First try: same flex/action container as ellipsis (most accurate for profile top-card).
    js_same_container = """
        const isVisible = (el) => {
            const r = el.getBoundingClientRect();
            const s = window.getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
        };
        const ellipsisCandidates = Array.from(document.querySelectorAll(
            "main button[aria-label*='More'], button[aria-label='More actions'], button[aria-label*='More'][aria-label*='actions']"
        )).filter(isVisible);
        // pick profile ellipsis (not navbar): first visible with y >= 120
        const ellipsis = ellipsisCandidates.find((el) => el.getBoundingClientRect().y >= 120);
        if (!ellipsis) return {clicked:false, reason:'ellipsis_not_found'};

        let actionContainer = ellipsis.parentElement;
        for (let i = 0; i < 5 && actionContainer; i++) {
            const btnCount = actionContainer.querySelectorAll("button").length;
            const r = actionContainer.getBoundingClientRect();
            if (btnCount >= 2 && r.y >= 120 && r.y <= 900) break;
            actionContainer = actionContainer.parentElement;
        }
        if (!actionContainer) return {clicked:false, reason:'action_container_not_found'};

        const btns = Array.from(actionContainer.querySelectorAll("button, a[role='button']"))
            .filter(isVisible);
        const found = [];
        for (const b of btns) {
            const txt = (b.innerText || '').replace(/\\s+/g, ' ').trim();
            const aria = (b.getAttribute('aria-label') || '').trim();
            const low = (txt + ' ' + aria).toLowerCase();
            if (!(low.includes('connect') || low.includes('invite'))) continue;
            found.push({el: b, text: txt, aria, x: b.getBoundingClientRect().x});
        }
        if (found.length === 0) return {clicked:false, reason:'no_connect_in_action_container'};

        found.sort((a, b) => a.x - b.x); // left-most in action row is usually Connect
        found[0].el.click();
        return {
            clicked: true,
            reason: 'same_action_container',
            text: found[0].text,
            aria: found[0].aria,
            candidates: found.length
        };
    """
    same_container_result = driver.execute_script(js_same_container) or {}
    logger.info("connect same-container result=%s", same_container_result)
    if bool(same_container_result.get("clicked")):
        return True, "same_action_container_as_ellipsis"

    ellipsis = _get_profile_ellipsis_rect(driver)
    if not ellipsis:
        return False, None

    ex = ellipsis["x"]
    ey = ellipsis["y"]

    candidates = driver.find_elements(
        By.XPATH,
        "//main//button[(contains(normalize-space(),'Connect') or contains(@aria-label,'Invite') or contains(@aria-label,'Connect'))]",
    )
    logger.info("connect-near-ellipsis candidates in main=%s", len(candidates))
    best = None
    best_score = 10**9
    for el in candidates:
        if not el.is_displayed() or not el.is_enabled():
            continue
        rect = driver.execute_script(
            "const r=arguments[0].getBoundingClientRect(); return {x:r.x,y:r.y,w:r.width,h:r.height};",
            el,
        )
        if not rect:
            continue
        x = float(rect.get("x", 0))
        y = float(rect.get("y", 0))
        # Left of ellipsis, close vertically, and in top card band.
        if x >= ex:
            continue
        if abs(y - ey) > 140:
            continue
        if y > 900:
            continue
        dx = ex - x
        dy = abs(ey - y)
        score = dx + (dy * 2.0)
        logger.info(
            "connect-near-ellipsis candidate text=%r aria=%r rect=%s score=%.1f",
            (el.text or "").strip(),
            (el.get_attribute("aria-label") or "").strip(),
            rect,
            score,
        )
        if score < best_score:
            best_score = score
            best = el

    if best is None:
        logger.info("no connect candidate found near ellipsis; trying row-neighbor fallback")
        row_fallback = driver.execute_script(
            """
            const isVisible = (el) => {
                const r = el.getBoundingClientRect();
                const s = window.getComputedStyle(el);
                return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
            };
            const ellipsis = Array.from(document.querySelectorAll(
                "main button[aria-label*='More'], button[aria-label='More actions'], button[aria-label*='More'][aria-label*='actions']"
            )).filter(isVisible).find((el) => el.getBoundingClientRect().y >= 120);
            if (!ellipsis) return {clicked:false, reason:'ellipsis_not_found'};

            const er = ellipsis.getBoundingClientRect();
            const allBtns = Array.from(document.querySelectorAll("button, a[role='button'], div[role='button']"))
                .filter(isVisible);
            const neighbors = [];
            for (const el of allBtns) {
                if (el === ellipsis) continue;
                const r = el.getBoundingClientRect();
                if (r.x >= er.x) continue; // must be left of ellipsis
                if (Math.abs(r.y - er.y) > 42) continue; // same action row
                if (r.x > 1000) continue; // avoid right rail
                const text = (el.innerText || '').replace(/\\s+/g, ' ').trim();
                const aria = (el.getAttribute('aria-label') || '').trim();
                const low = (text + ' ' + aria).toLowerCase();
                const looksConnect = low.includes('connect') || low.includes('invite');
                neighbors.push({
                    el,
                    text,
                    aria,
                    x: r.x,
                    y: r.y,
                    dx: er.x - r.x,
                    looksConnect,
                });
            }
            if (neighbors.length === 0) return {clicked:false, reason:'no_row_neighbors'};

            const connectLike = neighbors
                .filter((n) => n.looksConnect)
                .sort((a, b) => a.dx - b.dx);
            if (connectLike.length > 0) {
                connectLike[0].el.click();
                return {
                    clicked:true,
                    reason:'row_neighbor_connect_like',
                    text: connectLike[0].text,
                    aria: connectLike[0].aria,
                };
            }

            return {clicked:false, reason:'row_neighbors_found_but_no_connect_like'};
            """
        ) or {}
        logger.info("connect row-neighbor fallback result=%s", row_fallback)
        if bool(row_fallback.get("clicked")):
            return True, "row_neighbor_to_ellipsis"
        return False, None

    try:
        best.click()
    except Exception:
        driver.execute_script("arguments[0].click();", best)
    logger.info("connect clicked near ellipsis in profile action strip")
    return True, "near_profile_ellipsis"


def _click_profile_connect_button(driver: uc.Chrome, timeout_s: int) -> tuple[bool, str | None]:
    # 1) Most reliable: find the connect button nearest to profile ellipsis.
    clicked, source = _click_connect_near_ellipsis(driver, timeout_s=timeout_s)
    if clicked:
        return clicked, source

    # 2) Fallback: generic profile-section scans.
    wait = WebDriverWait(driver, timeout_s)
    candidates: list[tuple[str, str]] = [
        (
            "xpath_main_primary_connect",
            "//main//button[(normalize-space()='Connect' or contains(normalize-space(), 'Connect'))]",
        ),
        (
            "xpath_main_invite",
            "//main//button[contains(@aria-label, 'Invite') or contains(@aria-label, 'Connect')]",
        ),
    ]
    for label, xpath in candidates:
        try:
            elements = driver.find_elements(By.XPATH, xpath)
            logger.info("connect scan %s count=%s", label, len(elements))
            for el in elements:
                if not el.is_displayed() or not el.is_enabled():
                    continue
                text = (el.text or "").strip()
                aria = (el.get_attribute("aria-label") or "").strip()
                rect = driver.execute_script(
                    "const r=arguments[0].getBoundingClientRect(); return {x:r.x,y:r.y,w:r.width,h:r.height};",
                    el,
                )
                logger.info(
                    "connect candidate %s text=%r aria=%r rect=%s",
                    label,
                    text,
                    aria,
                    rect,
                )
                # Restrict to top-profile action area, avoid right rail suggestions.
                if not rect:
                    continue
                if rect.get("x", 9999) > 900:
                    continue
                if rect.get("y", 9999) > 900:
                    continue
                try:
                    el.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", el)
                logger.info("connect clicked from profile section via %s", label)
                return True, label
        except Exception as exc:
            logger.warning("connect click failed at %s err=%s", label, exc)
            continue

    # Fallback: JS click for top-most connect/invite button in profile top-card region.
    js = """
        const isVisible = (el) => {
            const r = el.getBoundingClientRect();
            const s = window.getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
        };
        const nodes = Array.from(document.querySelectorAll("main button"));
        const matches = [];
        for (const el of nodes) {
            if (!isVisible(el)) continue;
            const txt = (el.innerText || '').replace(/\\s+/g, ' ').trim().toLowerCase();
            const aria = (el.getAttribute('aria-label') || '').toLowerCase();
            if (txt.includes('connect') || aria.includes('invite') || aria.includes('connect')) {
                const r = el.getBoundingClientRect();
                if (r.x <= 900 && r.y <= 900) matches.push({el, y: r.y, x: r.x});
            }
        }
        matches.sort((a,b) => (a.y - b.y) || (a.x - b.x));
        if (matches.length > 0) {
            matches[0].el.click();
            return true;
        }
        return false;
    """
    clicked = bool(driver.execute_script(js))
    logger.info("connect js fallback clicked=%s", clicked)
    if clicked:
        return True, "js_main_connect_fallback"

    # Class-based fallback requested by user (brittle: LinkedIn class hashes may change).
    class_selector = (
        "button._5ad81ce7._8a429359._9f647156.f5e552a7.e905741c.fc99eeaf."
        "_246ab399._07694f74._66c688a8._4641a027._7ed5c3d6.dea28e8e."
        "_8f951094._0035c7f1.eb4e7a74._7a0c5b37._3e02b2d2._5ecdd028._159f99d5"
    )
    try:
        class_candidates = driver.find_elements(By.CSS_SELECTOR, class_selector)
        logger.info("connect class-selector candidates=%s", len(class_candidates))
        for idx, candidate in enumerate(class_candidates, start=1):
            if not candidate.is_displayed() or not candidate.is_enabled():
                continue
            text = (candidate.text or "").strip()
            aria = (candidate.get_attribute("aria-label") or "").strip()
            rect = driver.execute_script(
                "const r=arguments[0].getBoundingClientRect(); return {x:r.x,y:r.y,w:r.width,h:r.height};",
                candidate,
            )
            logger.info(
                "connect class-selector candidate[%s] text=%r aria=%r rect=%s",
                idx,
                text,
                aria,
                rect,
            )
            try:
                candidate.click()
            except Exception:
                driver.execute_script("arguments[0].click();", candidate)
            logger.info("connect clicked via class-selector fallback")
            return True, "class_selector_fallback"
    except Exception as exc:
        logger.warning("connect class-selector fallback failed err=%s", exc)
    return False, None


def _click_ellipsis_then_connect(driver: uc.Chrome, timeout_s: int) -> tuple[bool, str | None]:
    wait = WebDriverWait(driver, timeout_s)
    ellipsis_selectors = [
        "button[aria-label*='More'][aria-label*='actions']",
        "button[aria-label='More actions']",
        "main button[aria-label*='More']",
    ]
    for selector in ellipsis_selectors:
        count = len(driver.find_elements(By.CSS_SELECTOR, selector))
        logger.info("ellipsis selector=%r count=%s", selector, count)
        if count <= 0:
            continue
        try:
            btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, selector)))
            _human_pause(0.6, 1.4, label="before_ellipsis_click")
            _human_like_mouse_move(driver, btn, label="ellipsis_button")
            if not _human_like_click(driver, btn, label=f"ellipsis:{selector}"):
                raise RuntimeError("ellipsis click failed")
            logger.info("ellipsis clicked selector=%r", selector)
            break
        except Exception as exc:
            logger.warning("ellipsis click failed selector=%r err=%s", selector, exc)
            continue
    else:
        return False, None

    _human_pause(1.4, 2.8, label="after_ellipsis_click_before_menu_scan")
    connect_menu_selectors = [
        "//div[@role='menu']//span[normalize-space()='Connect']/ancestor::*[@role='menuitem' or self::button or self::a][1]",
        "//div[@role='menu']//*[self::button or self::a][normalize-space()='Connect']",
        "//div[@role='dialog']//span[normalize-space()='Connect']/ancestor::*[@role='menuitem' or self::button or self::a][1]",
        "//div[contains(@class,'artdeco-dropdown__content-inner')]//*[self::button or self::a][normalize-space()='Connect']",
    ]
    for xpath in connect_menu_selectors:
        try:
            items = driver.find_elements(By.XPATH, xpath)
            logger.info("menu connect xpath=%r count=%s", xpath, len(items))
            for item in items:
                if not item.is_displayed() or not item.is_enabled():
                    continue
                text = (item.text or "").strip()
                rect = driver.execute_script(
                    "const r=arguments[0].getBoundingClientRect(); return {x:r.x,y:r.y,w:r.width,h:r.height};",
                    item,
                )
                logger.info("menu connect candidate text=%r rect=%s", text, rect)
                _human_pause(0.7, 1.6, label="before_menu_connect_click")
                _human_like_mouse_move(driver, item, label="menu_connect_item")
                if not _human_like_click(driver, item, label=f"menu_connect:{xpath}"):
                    continue
                logger.info("connect clicked from ellipsis menu")
                _human_pause(0.5, 1.2, label="after_menu_connect_click")
                return True, "ellipsis_menu"
        except Exception:
            continue
    logger.info("connect not present in opened ellipsis menu")
    return False, None


def _click_add_note(driver: uc.Chrome, timeout_s: int) -> tuple[bool, str | None]:
    wait = WebDriverWait(driver, timeout_s)
    # Recorder-style first attempt: shadow/interop-aware "Add a note" discovery.
    add_note_js = """
        const isVisible = (el) => {
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

        for (const root of roots) {
            const nodes = deepNodes(root, "button, span, a, div[role='button']");
            const match = nodes.find((n) => {
                if (!isVisible(n)) return false;
                const txt = (n.innerText || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                const aria = ((n.getAttribute && n.getAttribute('aria-label')) || '').trim().toLowerCase();
                const cls = ((n.className && typeof n.className === 'string') ? n.className : '').toLowerCase();
                return (
                    txt === 'add a note' ||
                    txt.includes('add a note') ||
                    aria === 'add a note' ||
                    aria.includes('add a note') ||
                    (cls.includes('artdeco-button') && cls.includes('muted') && txt.includes('note'))
                );
            });
            if (match) {
                const btn = match.closest('button, a, div[role=\"button\"]') || match;
                const events = ['mouseover', 'mousedown', 'mouseup', 'click'];
                for (const ev of events) {
                    btn.dispatchEvent(new MouseEvent(ev, {bubbles:true, cancelable:true, view:window}));
                }
                return true;
            }
        }
        return false;
    """
    try:
        if bool(driver.execute_script(add_note_js)):
            logger.info("clicked Add a note via recorded-signature JS")
            return True, "recorded_signature_add_note_js"
    except Exception as exc:
        logger.info("add-note recorded-signature JS failed err=%s", exc)

    # LinkedIn first shows invite dialog containing Add a note button.
    selectors: list[tuple[str, str]] = [
        ("css_text_contains", "button:has-text('Add a note')"),
        ("xpath_button_text", "//button[normalize-space()='Add a note']"),
        ("xpath_span_text", "//span[normalize-space()='Add a note']/ancestor::button[1]"),
        ("css_aria", "button[aria-label*='Add a note']"),
        ("css_artdeco_muted", "button.artdeco-button--muted"),
        ("xpath_invitation_dialog_button", "//div[@role='dialog']//button[contains(., 'Add a note')]"),
    ]
    short_wait = WebDriverWait(driver, min(6, timeout_s))
    for label, query in selectors:
        try:
            if label == "css_text_contains":
                btn = short_wait.until(
                    lambda d: d.execute_script(
                        """
                        const isVisible = (el) => {
                            const r = el.getBoundingClientRect();
                            const s = window.getComputedStyle(el);
                            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
                        };
                        const nodes = Array.from(document.querySelectorAll('button'));
                        const target = nodes.find((n) => isVisible(n) && (n.innerText || '').toLowerCase().includes('add a note'));
                        return target || null;
                        """
                    )
                )
            elif query.startswith("//"):
                btn = short_wait.until(EC.element_to_be_clickable((By.XPATH, query)))
            else:
                btn = short_wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, query)))
                # For generic muted button selector, ensure it is actually Add a note.
                if label == "css_artdeco_muted":
                    txt = (btn.text or "").strip().lower()
                    aria = (btn.get_attribute("aria-label") or "").strip().lower()
                    if "add a note" not in txt and "add a note" not in aria:
                        continue
            if _human_like_click(driver, btn, label=f"add_note:{label}"):
                logger.info("clicked Add a note via %s", label)
                return True, label
        except Exception as exc:
            logger.info("add-note not found via %s err=%s", label, exc)
    return False, None


def _has_invitation_dialog(driver: uc.Chrome) -> bool:
    js = """
        const isVisible = (el) => {
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
                try {
                    out.push(...Array.from(node.querySelectorAll(selector)));
                } catch (_) {}
                const all = node.querySelectorAll ? node.querySelectorAll('*') : [];
                for (const el of all) {
                    if (el && el.shadowRoot) stack.push(el.shadowRoot);
                }
            }
            return out;
        };

        const roots = [document];
        const interop = document.getElementById('interop-outlet');
        if (interop) roots.push(interop);
        if (interop && interop.shadowRoot) roots.push(interop.shadowRoot);

        const textNodes = [];
        for (const root of roots) {
            textNodes.push(...deepNodes(root, 'button, span, a, div[role=\"dialog\"], [role=\"alert\"]'));
        }

        const addNoteBtn = textNodes.find((el) => isVisible(el) && (el.innerText || '').trim() === 'Add a note');
        if (addNoteBtn) return true;

        const sendWithoutNote = textNodes
            .find((el) => isVisible(el) && (el.innerText || '').toLowerCase().includes('send without'));
        if (sendWithoutNote) return true;

        const dialogs = textNodes.filter((el) => (el.getAttribute && el.getAttribute('role') === 'dialog') && isVisible(el));
        for (const d of dialogs) {
            const txt = (d.innerText || '').toLowerCase();
            if (txt.includes('invitation') || txt.includes('add a note')) return true;
        }
        return false;
    """
    try:
        return bool(driver.execute_script(js))
    except Exception:
        return False


def _capture_post_click_dom_diagnostics(driver: uc.Chrome) -> dict[str, Any]:
    js = """
        const active = document.activeElement;
        const activeRect = active && active.getBoundingClientRect ? active.getBoundingClientRect() : null;
        const dialogs = Array.from(document.querySelectorAll("div[role='dialog']"));
        const visibleDialogs = dialogs.filter((d) => {
            const r = d.getBoundingClientRect();
            const s = window.getComputedStyle(d);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
        });

        const pointX = (window.__lastConnectClickRect && typeof window.__lastConnectClickRect.x === 'number')
            ? window.__lastConnectClickRect.x + Math.max(1, Math.floor((window.__lastConnectClickRect.w || 2) / 2))
            : Math.floor(window.innerWidth / 2);
        const pointY = (window.__lastConnectClickRect && typeof window.__lastConnectClickRect.y === 'number')
            ? window.__lastConnectClickRect.y + Math.max(1, Math.floor((window.__lastConnectClickRect.h || 2) / 2))
            : Math.floor(window.innerHeight / 2);

        const stack = document.elementsFromPoint(pointX, pointY).slice(0, 8).map((el) => ({
            tag: el.tagName ? el.tagName.toLowerCase() : '',
            id: el.id || '',
            className: (el.className && typeof el.className === 'string') ? el.className : '',
            role: el.getAttribute ? (el.getAttribute('role') || '') : '',
            ariaLabel: el.getAttribute ? (el.getAttribute('aria-label') || '') : '',
            text: (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 120),
        }));

        return {
            activeElement: active ? {
                tag: active.tagName ? active.tagName.toLowerCase() : '',
                id: active.id || '',
                className: (active.className && typeof active.className === 'string') ? active.className : '',
                role: active.getAttribute ? (active.getAttribute('role') || '') : '',
                ariaLabel: active.getAttribute ? (active.getAttribute('aria-label') || '') : '',
                text: (active.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 120),
                rect: activeRect ? {
                    x: Math.round(activeRect.x), y: Math.round(activeRect.y),
                    w: Math.round(activeRect.width), h: Math.round(activeRect.height)
                } : null,
            } : null,
            dialogCountTotal: dialogs.length,
            dialogCountVisible: visibleDialogs.length,
            pointChecked: { x: pointX, y: pointY },
            elementsFromPoint: stack,
        };
    """
    try:
        return driver.execute_script(js) or {}
    except Exception as exc:
        return {"error": str(exc)}


def _classify_post_connect_state(driver: uc.Chrome) -> dict[str, Any]:
    js = """
        const isVisible = (el) => {
            const r = el.getBoundingClientRect();
            const s = window.getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
        };
        const textOf = (el) => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();

        const visibleButtons = Array.from(document.querySelectorAll('button, a[role=\"button\"], div[role=\"button\"], a'))
            .filter(isVisible);

        const hasPending = visibleButtons.some((el) => {
            const t = textOf(el).toLowerCase();
            const a = (el.getAttribute('aria-label') || '').toLowerCase();
            return t.includes('pending') || a.includes('pending');
        });
        if (hasPending) return { state: 'pending', confidence: 'high' };

        const hasMessage = visibleButtons.some((el) => {
            const t = textOf(el).toLowerCase();
            const a = (el.getAttribute('aria-label') || '').toLowerCase();
            return t === 'message' || t.startsWith('message ') || a.includes('message');
        });
        if (hasMessage) return { state: 'message_available', confidence: 'medium' };

        const dialogNodes = Array.from(document.querySelectorAll(\"div[role='dialog']\")).filter(isVisible);
        const dialogText = dialogNodes.map(textOf).join(' | ').toLowerCase();
        if (dialogText.includes('add a note') || dialogText.includes('invitation')) {
            return { state: 'invitation_dialog_open', confidence: 'high' };
        }

        const toastCandidates = Array.from(document.querySelectorAll(\"[role='alert'], .artdeco-toast-item, .artdeco-inline-feedback\"))
            .filter(isVisible)
            .map(textOf)
            .join(' | ')
            .toLowerCase();
        if (
            toastCandidates.includes('invitation sent') ||
            toastCandidates.includes('request sent') ||
            toastCandidates.includes('sent')
        ) {
            return { state: 'invite_sent_toast', confidence: 'medium', toastText: toastCandidates.slice(0, 300) };
        }

        return { state: 'unknown', confidence: 'low' };
    """
    try:
        return driver.execute_script(js) or {"state": "unknown", "confidence": "low"}
    except Exception as exc:
        return {"state": "unknown", "confidence": "low", "error": str(exc)}


def _detect_pending_connection_request(driver: uc.Chrome) -> bool:
    """Detect whether a visible Pending control is present in profile top actions."""
    js = """
        const isVisible = (el) => {
            if (!el) return false;
            const r = el.getBoundingClientRect();
            const s = window.getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
        };
        const norm = (t) => (t || '').replace(/\\s+/g,' ').trim().toLowerCase();
        const main = document.querySelector('main') || document;
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
                if (txt === 'pending' || aria.includes('pending') || title === 'pending') {
                    const r = el.getBoundingClientRect();
                    // Avoid right-rail and lower-page recommendation cards.
                    if (r.x > 1000 || r.y > 900) continue;
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


def _detect_interaction_blockers(driver: uc.Chrome) -> dict[str, Any]:
    """Detect common LinkedIn UI states that block interaction/clicking."""
    js = """
        const isVisible = (el) => {
            const r = el.getBoundingClientRect();
            const s = window.getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden' && s.pointerEvents !== 'none';
        };
        const textOf = (el) => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
        const low = (s) => (s || '').toLowerCase();
        const bodyText = low((document.body && document.body.innerText) || '');

        const challengeKeywords = [
            'verify now',
            'security check',
            'suspicious',
            'unusual activity',
            'confirm it\\'s you',
            'captcha',
            'challenge',
            'restricted',
            'temporarily blocked',
        ];
        const challengeSignals = challengeKeywords.filter((k) => bodyText.includes(k));

        const visibleDialogs = Array.from(document.querySelectorAll("div[role='dialog']")).filter(isVisible);
        const visibleDialogText = visibleDialogs.map((d) => textOf(d)).join(' | ').toLowerCase();

        const pendingSignal = Array.from(document.querySelectorAll("button, a[role='button'], div[role='button'], a"))
            .filter(isVisible)
            .some((el) => {
                const t = low(textOf(el));
                const aria = low(el.getAttribute('aria-label') || '');
                return t.includes('pending') || aria.includes('pending');
            });

        const sendButtonStates = Array.from(document.querySelectorAll("div[role='dialog'] button"))
            .map((b) => {
                const t = low(textOf(b));
                const aria = low(b.getAttribute('aria-label') || '');
                const cls = low((typeof b.className === 'string' ? b.className : ''));
                const isSend = t === 'send' || t.includes('send invitation') || aria.includes('send');
                if (!isSend) return null;
                const r = b.getBoundingClientRect();
                return {
                    disabled: !!b.disabled || b.getAttribute('aria-disabled') === 'true',
                    visible: isVisible(b),
                    cls: cls.slice(0, 180),
                    rect: {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)},
                };
            })
            .filter(Boolean);

        // Overlay heuristic: large fixed element with high z-index covering viewport center.
        const centerX = Math.floor(window.innerWidth / 2);
        const centerY = Math.floor(window.innerHeight / 2);
        const fixedCandidates = Array.from(document.querySelectorAll("div,section,aside,main"))
            .filter(isVisible)
            .map((el) => {
                const s = window.getComputedStyle(el);
                const r = el.getBoundingClientRect();
                return {el, pos: s.position, zi: parseInt(s.zIndex || '0', 10), r, cls: el.className || ''};
            })
            .filter((x) =>
                (x.pos === 'fixed' || x.pos === 'sticky') &&
                Number.isFinite(x.zi) &&
                x.zi >= 1000 &&
                x.r.left <= centerX && x.r.right >= centerX &&
                x.r.top <= centerY && x.r.bottom >= centerY &&
                x.r.width >= Math.floor(window.innerWidth * 0.5) &&
                x.r.height >= Math.floor(window.innerHeight * 0.25)
            )
            .slice(0, 5)
            .map((x) => ({
                position: x.pos,
                zIndex: x.zi,
                cls: (typeof x.cls === 'string' ? x.cls : '').slice(0, 180),
                rect: {
                    x: Math.round(x.r.x), y: Math.round(x.r.y),
                    w: Math.round(x.r.width), h: Math.round(x.r.height)
                },
            }));

        return {
            url: window.location.href,
            pendingSignal,
            challengeSignalDetected: challengeSignals.length > 0,
            challengeSignals,
            visibleDialogCount: visibleDialogs.length,
            visibleDialogText: visibleDialogText.slice(0, 300),
            sendButtonStates,
            fixedOverlayCandidates: fixedCandidates,
            timestampMs: Date.now(),
        };
    """
    try:
        return driver.execute_script(js) or {}
    except Exception as exc:
        return {"error": str(exc)}


def _retry_connect_click_recorded_signature(driver: uc.Chrome) -> bool:
    js = """
        const isVisible = (el) => {
            const r = el.getBoundingClientRect();
            const s = window.getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
        };
        const anchors = Array.from(document.querySelectorAll('a[aria-label]')).filter(isVisible);
        const target = anchors.find((a) => {
            const aria = (a.getAttribute('aria-label') || '').toLowerCase().trim();
            const r = a.getBoundingClientRect();
            return aria.startsWith('invite ') && aria.endsWith(' to connect') && r.x < 1000;
        });
        if (!target) return false;

        const span = target.querySelector('span');
        const clickNode = span || target;
        const rr = target.getBoundingClientRect();
        window.__lastConnectClickRect = {
            x: Math.round(rr.x), y: Math.round(rr.y), w: Math.round(rr.width), h: Math.round(rr.height)
        };
        const events = ['mouseover', 'mousedown', 'mouseup', 'click'];
        for (const ev of events) {
            clickNode.dispatchEvent(new MouseEvent(ev, {bubbles:true, cancelable:true, view:window}));
        }
        return true;
    """
    try:
        clicked = bool(driver.execute_script(js))
        logger.info("connect recorded-signature retry click dispatched=%s", clicked)
        return clicked
    except Exception as exc:
        logger.warning("connect recorded-signature retry failed err=%s", exc)
        return False


def _classify_post_send_state(driver: uc.Chrome) -> dict[str, Any]:
    js = """
        const isVisible = (el) => {
            const r = el.getBoundingClientRect();
            const s = window.getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
        };
        const textOf = (el) => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
        const lower = (s) => (s || '').toLowerCase();

        const visibleDialogs = Array.from(document.querySelectorAll("div[role='dialog']")).filter(isVisible);
        const dialogText = visibleDialogs.map((d) => textOf(d)).join(' | ').toLowerCase();

        const verifyNowVisible = visibleDialogs.some((d) => {
            const txt = lower(textOf(d));
            return txt.includes('verify now');
        });
        if (verifyNowVisible) return { state: 'verify_now_modal_open', confidence: 'high' };

        const invitationDialogStillOpen = visibleDialogs.some((d) => {
            const txt = lower(textOf(d));
            const hasTextarea = d.querySelector('textarea') !== null;
            const hasSendButton = Array.from(d.querySelectorAll("button, a[role='button'], div[role='button']"))
                .some((el) => {
                    const t = lower(textOf(el));
                    const a = lower(el.getAttribute('aria-label') || '');
                    return t === 'send' || t.includes(' send') || a === 'send' || a.includes('send');
                });
            return hasTextarea || hasSendButton || txt.includes('add a note') || txt.includes('invitation');
        });
        if (invitationDialogStillOpen) {
            return { state: 'invitation_dialog_still_open', confidence: 'high' };
        }

        const pendingVisible = Array.from(document.querySelectorAll("button, a[role='button'], div[role='button'], a"))
            .filter(isVisible)
            .some((el) => {
                const t = lower(textOf(el));
                const a = lower(el.getAttribute('aria-label') || '');
                return t.includes('pending') || a.includes('pending');
            });
        if (pendingVisible) return { state: 'pending', confidence: 'medium' };

        const toastText = Array.from(document.querySelectorAll("[role='alert'], .artdeco-toast-item, .artdeco-inline-feedback"))
            .filter(isVisible)
            .map(textOf)
            .join(' | ')
            .toLowerCase();
        if (
            toastText.includes('invitation sent') ||
            toastText.includes('request sent') ||
            toastText.includes('invitation has been sent')
        ) {
            return { state: 'invite_sent_toast', confidence: 'medium', toastText: toastText.slice(0, 300) };
        }

        if (dialogText) {
            return { state: 'unexpected_modal_open', confidence: 'medium', dialogText: dialogText.slice(0, 300) };
        }
        return { state: 'unknown', confidence: 'low' };
    """
    try:
        return driver.execute_script(js) or {"state": "unknown", "confidence": "low"}
    except Exception as exc:
        return {"state": "unknown", "confidence": "low", "error": str(exc)}


def _retry_send_click_recorded_signature(driver: uc.Chrome) -> bool:
    js = """
        const isVisible = (el) => {
            const r = el.getBoundingClientRect();
            const s = window.getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
        };
        const textOf = (el) => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
        const lower = (s) => (s || '').toLowerCase();

        const visibleDialogs = Array.from(document.querySelectorAll("div[role='dialog']")).filter(isVisible);
        for (const dialog of visibleDialogs) {
            const buttons = Array.from(dialog.querySelectorAll("button, a[role='button'], div[role='button'], span"));
            const target = buttons.find((n) => {
                if (!isVisible(n)) return false;
                const txt = lower(textOf(n));
                const aria = lower((n.getAttribute && n.getAttribute('aria-label')) || '');
                const cls = ((n.className && typeof n.className === 'string') ? n.className : '').toLowerCase();
                if (txt === 'send' || aria === 'send') return true;
                if (txt.includes('send invitation') || aria.includes('send invitation')) return true;
                if (cls.includes('artdeco-button--primary') && txt.includes('send')) return true;
                return false;
            });
            if (!target) continue;
            const btn = target.closest("button, a[role='button'], div[role='button']") || target;
            if (btn.disabled) continue;
            const events = ['mouseover', 'mousedown', 'mouseup', 'click'];
            for (const ev of events) {
                btn.dispatchEvent(new MouseEvent(ev, {bubbles:true, cancelable:true, view:window}));
            }
            return true;
        }
        return false;
    """
    try:
        clicked = bool(driver.execute_script(js))
        logger.info("send recorded-signature retry click dispatched=%s", clicked)
        return clicked
    except Exception as exc:
        logger.warning("send recorded-signature retry failed err=%s", exc)
        return False


def _fill_note_and_send(driver: uc.Chrome, timeout_s: int, note_text: str) -> tuple[bool, str | None]:
    wait = WebDriverWait(driver, timeout_s)
    # Wait for modal to switch from "Add a note" state to textarea editor state.
    editor_ready = False
    try:
        editor_ready = bool(
            WebDriverWait(driver, min(10, timeout_s)).until(
                lambda d: d.execute_script(
                    """
                    const isVisible = (el) => {
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
                    const areas = roots.flatMap((r) => deepNodes(r, 'textarea')).filter(isVisible);
                    return areas.length > 0;
                    """
                )
            )
        )
    except Exception:
        editor_ready = False
    logger.info("note editor ready=%s", editor_ready)

    # Shadow-DOM aware textarea discovery (LinkedIn interop outlet) with human-like typing.
    try:
        filled = _type_text_human_like_shadow_aware(driver, note_text)
        if filled:
            logger.info("note filled via shadow-aware human-like typing chars=%s", len(note_text))
        else:
            logger.info("shadow-aware textarea fill unavailable; trying selenium fallback")
    except Exception as exc:
        logger.info("shadow-aware textarea fill failed err=%s", exc)
        filled = False

    if filled:
        _human_pause(0.6, 1.4, label="after_note_fill")
    else:
        textarea_xpaths = [
            "//textarea",
            "//div[@role='dialog']//textarea",
        ]
        for xpath in textarea_xpaths:
            try:
                box = wait.until(EC.visibility_of_element_located((By.XPATH, xpath)))
                box.clear()
                _type_text_human_like_webelement(box, note_text)
                logger.info("note filled via selenium human-like typing chars=%s", len(note_text))
                _human_pause(0.6, 1.4, label="after_note_fill")
                break
            except Exception:
                continue
        else:
            logger.warning("note textarea not found")
            return False, None

    send_xpaths = [
        # Most specific target from observed DOM.
        "//div[@role='dialog']//button[@aria-label='Send invitation' and contains(@class,'artdeco-button') and contains(@class,'artdeco-button--primary')]",
        "//div[@role='dialog']//button[@aria-label='Send invitation']",
        # Strict modal-scoped selector requested by user: span.artdeco-button__text -> parent button.
        "//div[@role='dialog']//button[.//span[contains(@class,'artdeco-button__text') and normalize-space()='Send']]",
        # Keep modal-scoped text fallbacks.
        "//div[@role='dialog']//button[normalize-space()='Send']",
        "//div[@role='dialog']//span[normalize-space()='Send']/ancestor::button[1]",
    ]
    pre_send_marker_path = _capture_pre_send_click_debug_screenshot(driver)
    # Important: marker can resolve Send in shadow DOM while Selenium XPath cannot.
    # Try shadow-aware click immediately before expensive XPath waits.
    if _click_send_shadow_aware(driver):
        registered = _wait_send_registration(driver, timeout_s=2.8)
        logger.info("clicked Send via primary shadow-aware JS path registered=%s", registered)
        if registered:
            return True, "shadow_primary_send_js"
        logger.info("primary shadow-aware send click did not register, continuing fallbacks")

    if not pre_send_marker_path:
        modal_snapshot = _capture_invitation_modal_snapshot(driver)
        logger.info("pre-send modal snapshot=%s", modal_snapshot)
        try:
            debug_dir = _project_root_dir() / "debug_output"
            debug_dir.mkdir(parents=True, exist_ok=True)
            raw_path = debug_dir / f"send_preclick_raw_{int(time.time() * 1000)}.png"
            driver.save_screenshot(str(raw_path))
            logger.info("pre-send raw screenshot saved=%s", raw_path)
        except Exception as exc:
            logger.info("pre-send raw screenshot save failed err=%s", exc)
        if _click_send_shadow_aware(driver):
            registered = _wait_send_registration(driver, timeout_s=2.8)
            logger.info("clicked Send via fast shadow-aware JS fallback after marker miss registered=%s", registered)
            if registered:
                return True, "shadow_fast_fallback_send_js"
    # Prefer Selenium mouse-like click on modal-scoped button first.
    for xpath in send_xpaths:
        try:
            btn = wait.until(EC.element_to_be_clickable((By.XPATH, xpath)))
            _human_pause(0.3, 0.9, label="before_send_click")
            if _human_like_click(driver, btn, label=f"send:{xpath}"):
                logger.info("clicked Send via modal-scoped selenium selector=%s", xpath)
                return True, xpath
        except Exception:
            continue

    # Recorder-style JS fallback (still strictly modal scoped).
    try:
        clicked_js = bool(
            driver.execute_script(
                """
                const isVisible = (el) => {
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
                const dialogs = Array.from(document.querySelectorAll("div[role='dialog']")).filter(isVisible);
                for (const dialog of dialogs) {
                    const nodes = deepNodes(dialog, "span.artdeco-button__text, button, a, div[role='button']");
                    const sendNode = nodes.find((n) => {
                        if (!isVisible(n)) return false;
                        const txt = (n.innerText || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                        const aria = ((n.getAttribute && n.getAttribute('aria-label')) || '').trim().toLowerCase();
                        const cls = ((n.className && typeof n.className === 'string') ? n.className : '').toLowerCase();
                        if (aria === 'send invitation') return true;
                        if (aria.includes('send invitation') && cls.includes('artdeco-button')) return true;
                        if (cls.includes('artdeco-button__text') && txt === 'send') return true;
                        if (txt === 'send' || aria === 'send') return true;
                        return false;
                    });
                    if (!sendNode) continue;
                    const btn = sendNode.closest('button, a, div[role=\"button\"]');
                    if (!btn || btn.disabled || !isVisible(btn)) continue;
                    const events = ['mouseover', 'mousedown', 'mouseup', 'click'];
                    for (const ev of events) {
                        btn.dispatchEvent(new MouseEvent(ev, {bubbles:true, cancelable:true, view:window}));
                    }
                    return true;
                }
                return false;
                """
            )
        )
        if clicked_js:
            logger.info("clicked Send via recorded-signature JS")
            return True, "recorded_signature_send_js"
    except Exception as exc:
        logger.info("send recorded-signature JS failed err=%s", exc)

    logger.warning("send button not found or not clickable in invitation dialog")
    return False, None


def _click_verify_now_if_present(driver: uc.Chrome) -> tuple[bool, str | None]:
    """Optional post-send step: click Verify now if LinkedIn shows it."""
    try:
        clicked = bool(
            driver.execute_script(
                """
                const isVisible = (el) => {
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

                for (const root of roots) {
                    const nodes = deepNodes(root, "button, span, a, div[role='button']");
                    const target = nodes.find((n) => {
                        if (!isVisible(n)) return false;
                        const txt = (n.innerText || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                        const aria = ((n.getAttribute && n.getAttribute('aria-label')) || '').trim().toLowerCase();
                        return txt === 'verify now' || txt.includes('verify now') || aria.includes('verify now');
                    });
                    if (!target) continue;
                    const btn = target.closest('button, a, div[role=\"button\"]') || target;
                    const events = ['mouseover', 'mousedown', 'mouseup', 'click'];
                    for (const ev of events) {
                        btn.dispatchEvent(new MouseEvent(ev, {bubbles:true, cancelable:true, view:window}));
                    }
                    return true;
                }
                return false;
                """
            )
        )
        if clicked:
            logger.info("clicked Verify now popup button")
            return True, "verify_now_js"
    except Exception as exc:
        logger.info("verify-now JS click attempt failed err=%s", exc)
    return False, None


def _send_connection_request_with_driver(
    driver: uc.Chrome,
    profile_url: str,
    note_text: str,
    *,
    initial_wait_s: float = 4.0,
    timeout_s: int = 20,
) -> dict[str, Any]:
    """Core connection-request logic on an already-open browser (no launch/quit)."""
    result: dict[str, Any] = {
        "profile_url": profile_url,
        "connect_clicked": False,
        "connect_source": None,
        "add_note_clicked": False,
        "send_clicked": False,
        "note_text": note_text,
        "pending_detected_after_connect": False,
        "connect_attempts": 0,
    }

    driver.get(profile_url)
    time.sleep(max(0.0, initial_wait_s))
    _human_pause(1.2, 2.8, label="after_profile_load")
    logger.info("landed url=%s title=%s", driver.current_url, driver.title)

    clicked = False
    source: str | None = None
    max_connect_attempts = 3  # initial + 2 retries
    for attempt in range(1, max_connect_attempts + 1):
        result["connect_attempts"] = attempt
        clicked, source = _click_profile_connect_button(driver, timeout_s=timeout_s)
        if not clicked:
            diagnostics = _diagnose_connect_candidates(driver)
            logger.info("connect diagnostics candidates=%s", len(diagnostics))
            for idx, item in enumerate(diagnostics, start=1):
                logger.info("connect diagnostics[%s]=%s", idx, item)
            clicked, source = _click_ellipsis_then_connect(driver, timeout_s=timeout_s)

        result["connect_clicked"] = clicked
        result["connect_source"] = source
        if not clicked:
            logger.warning("connect button not clicked from profile or ellipsis menu (attempt %s/%s)", attempt, max_connect_attempts)
            if attempt < max_connect_attempts:
                _human_pause(1.8, 3.2, label="before_connect_retry_after_click_miss")
            continue

        _human_pause(3.5, 6.5, label="after_connect_click")

        # Prefer invitation modal path first. If it is open, we should click
        # Add a note and send, rather than short-circuiting as "pending".
        if _has_invitation_dialog(driver):
            logger.info(
                "invitation dialog detected after connect click (attempt %s/%s); proceeding to add-note flow",
                attempt,
                max_connect_attempts,
            )
            break

        if _detect_pending_connection_request(driver):
            logger.info("pending detected after connect click (attempt %s/%s)", attempt, max_connect_attempts)
            result["pending_detected_after_connect"] = True
            result["send_clicked"] = True
            return result

        logger.info("pending not detected after connect click (attempt %s/%s)", attempt, max_connect_attempts)
        if attempt < max_connect_attempts:
            _human_pause(1.8, 3.2, label="before_connect_retry_after_no_pending")
            continue
        break

    if not clicked:
        return result

    post_click_dom = _capture_post_click_dom_diagnostics(driver)
    post_click_state = _classify_post_connect_state(driver)
    blockers_after_connect = _detect_interaction_blockers(driver)
    result["post_click_dom_diagnostics"] = post_click_dom
    result["post_click_state"] = post_click_state
    result["ui_blockers_after_connect"] = blockers_after_connect
    logger.info("post-click dom diagnostics=%s", post_click_dom)
    logger.info("post-click state classification=%s", post_click_state)
    logger.info("ui blockers after connect=%s", blockers_after_connect)

    invite_open = _has_invitation_dialog(driver)
    result["invitation_dialog_opened"] = invite_open
    if not invite_open:
        logger.info("invitation dialog not detected after connect click; retrying connect click")
        _retry_connect_click_recorded_signature(driver)
        _human_pause(3.0, 6.0, label="after_connect_retry")
        post_click_dom_retry = _capture_post_click_dom_diagnostics(driver)
        post_click_state_retry = _classify_post_connect_state(driver)
        result["post_click_dom_diagnostics_after_retry"] = post_click_dom_retry
        result["post_click_state_after_retry"] = post_click_state_retry
        logger.info("post-click dom diagnostics after retry=%s", post_click_dom_retry)
        logger.info("post-click state classification after retry=%s", post_click_state_retry)
        invite_open = _has_invitation_dialog(driver)
        result["invitation_dialog_opened_after_retry"] = invite_open
        if not invite_open:
            logger.warning("invitation dialog still not detected after retry")
            return result

    _human_pause(1.8, 3.5, label="before_add_note_click")
    add_note_clicked, add_note_source = _click_add_note(driver, timeout_s=timeout_s)
    result["add_note_clicked"] = add_note_clicked
    result["add_note_source"] = add_note_source
    if not add_note_clicked:
        logger.warning("add note dialog action not available")
        return result

    _human_pause(1.8, 3.8, label="before_note_fill_and_send")
    blockers_before_send = _detect_interaction_blockers(driver)
    result["ui_blockers_before_send"] = blockers_before_send
    logger.info("ui blockers before send=%s", blockers_before_send)
    send_clicked, send_selector = _fill_note_and_send(driver, timeout_s=timeout_s, note_text=note_text)
    result["send_clicked"] = send_clicked
    result["send_selector"] = send_selector
    post_send_state = _classify_post_send_state(driver)
    blockers_after_send = _detect_interaction_blockers(driver)
    result["post_send_state"] = post_send_state
    result["ui_blockers_after_send"] = blockers_after_send
    logger.info("post-send state classification=%s", post_send_state)
    logger.info("ui blockers after send=%s", blockers_after_send)

    if send_clicked and post_send_state.get("state") in {
        "invitation_dialog_still_open",
        "unexpected_modal_open",
        "unknown",
    }:
        logger.info(
            "post-send state=%s; retrying send click with recorded-signature fallback",
            post_send_state.get("state"),
        )
        _retry_send_click_recorded_signature(driver)
        _human_pause(1.5, 3.0, label="after_send_retry")
        post_send_state_retry = _classify_post_send_state(driver)
        result["post_send_state_after_retry"] = post_send_state_retry
        logger.info("post-send state classification after retry=%s", post_send_state_retry)

    if send_clicked:
        _human_pause(0.8, 1.8, label="before_optional_verify_now")
        verify_clicked, verify_source = _click_verify_now_if_present(driver)
        result["verify_now_clicked"] = verify_clicked
        result["verify_now_source"] = verify_source
    else:
        result["verify_now_clicked"] = False
        result["verify_now_source"] = None
    result["current_url"] = driver.current_url
    result["page_title"] = driver.title
    logger.info("connection request workflow completed send_clicked=%s", send_clicked)
    return result


def send_connection_request_sync(
    profile_url: str,
    *,
    storage_state_path: str | Path = DEFAULT_STORAGE_PATH,
    headless: bool = False,
    initial_wait_s: float = 4.0,
    wait_before_close_s: float = 18.0,
    timeout_s: int = 20,
) -> dict[str, Any]:
    """Send LinkedIn connection request with note for one profile URL."""
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

    chrome_kwargs: dict[str, Any] = {"options": options}
    version_main = _detect_chrome_major_version()
    if version_main:
        chrome_kwargs["version_main"] = version_main
    if os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("PORT"):
        chrome_kwargs["browser_executable_path"] = "/usr/bin/chromium"
        chrome_kwargs["driver_executable_path"] = "/usr/bin/chromedriver"

    logger.info("connection sender launch headless=%s version_main=%s", headless, version_main)
    driver = uc.Chrome(**chrome_kwargs)
    try:
        injected = _inject_linkedin_cookies(driver, storage_data)
        logger.info("cookies injected=%s", injected)
        note = _random_note_text(200)
        result = _send_connection_request_with_driver(
            driver,
            profile_url,
            note,
            initial_wait_s=initial_wait_s,
            timeout_s=timeout_s,
        )
        result["cookie_count_injected"] = injected
        return result
    finally:
        if wait_before_close_s > 0:
            logger.info("waiting %.1fs before closing browser", wait_before_close_s)
            time.sleep(wait_before_close_s)
        driver.quit()
