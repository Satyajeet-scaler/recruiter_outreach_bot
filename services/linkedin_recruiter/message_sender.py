"""LinkedIn profile message sender using undetected-chromedriver.

Load / timing:
- After navigation: wait for document complete, main column visible, then rAF + requestIdleCallback
  (best-effort \"scripts settled\"), randomized idle pause — before any Message interaction.

Click strategy:
1) Shadow-aware scan for Message controls; score like the green \"preferred\" overlay.
2) Primary: connection_request_sender._human_like_click (pointer + WebElement.click).
3) Fallback: in-page pointer-event sequence + target.click().
4) Last resort only: HTMLElement.click() on the WebElement (bypasses z-order; less human-like).

Result JSON may include message_click_strategies with short labels for what ran.
"""

from __future__ import annotations

import logging
import os
import random
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support.ui import WebDriverWait
import undetected_chromedriver as uc

from services.linkedin_recruiter.ellipsis_menu_service import (
    DEFAULT_STORAGE_PATH,
    _inject_linkedin_cookies,
    _load_storage,
)
from services.linkedin_recruiter.connection_request_sender import (
    _dispatch_mouse_sequence_js,
    _human_like_click,
    _human_pause,
    _type_text_human_like_webelement,
)

logger = logging.getLogger(__name__)

# After navigation: let LinkedIn's bundles paint and idle callbacks run before we touch UI.
_HUMAN_IDLE_AFTER_SCRIPTS_MIN_S = 2.0
_HUMAN_IDLE_AFTER_SCRIPTS_MAX_S = 5.8


def _project_root_dir() -> Path:
    return Path(__file__).resolve().parents[2]


def _capture_message_candidates_marked_screenshot(driver: uc.Chrome) -> tuple[str | None, dict[str, Any]]:
    """Draw overlays on all Message button candidates and save screenshot for verification."""
    try:
        meta = driver.execute_script(
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
            const textOf = (el) => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
            const roots = [document];
            const interop = document.getElementById('interop-outlet');
            if (interop) roots.push(interop);
            if (interop && interop.shadowRoot) roots.push(interop.shadowRoot);

            const scored = [];
            const seenOwners = new WeakSet();
            for (const root of roots) {
                const nodes = deepNodes(root, "button, a[role='button'], div[role='button'], a");
                for (const n of nodes) {
                    if (!isVisible(n)) continue;
                    const txt = textOf(n).toLowerCase();
                    const aria = (n.getAttribute('aria-label') || '').toLowerCase();
                    if (!(txt === 'message' || txt.startsWith('message ') || aria.includes('message'))) continue;
                    const owner = n.closest("button, a[role='button'], div[role='button'], a") || n;
                    if (seenOwners.has(owner)) continue;
                    seenOwners.add(owner);
                    const r = owner.getBoundingClientRect();
                    const score =
                        (owner.closest('main section') ? 100 : 0) +
                        (owner.closest('main') ? 35 : 0) +
                        (!owner.closest('#msg-overlay, .msg-overlay-list-bubble, .msg-overlay-container') ? 25 : 0) +
                        (r.y >= 40 && r.y < 900 ? 15 : 0);
                    scored.push({ owner, score, inMain: !!owner.closest('main'), inTopCard: !!owner.closest('main section') });
                }
            }
            scored.sort((a, b) => b.score - a.score);

            const stamp = Date.now();
            const marks = [];
            scored.forEach((item, idx) => {
                const el = item.owner;
                el.scrollIntoView({block:'center'});
                const r = el.getBoundingClientRect();
                const preferred = idx === 0;
                const border = preferred ? '#00c853' : '#ff9100';

                const mark = document.createElement('div');
                mark.id = `__msg_marker_${stamp}_${idx}`;
                mark.style.position = 'fixed';
                mark.style.left = `${Math.max(0, r.left - 3)}px`;
                mark.style.top = `${Math.max(0, r.top - 3)}px`;
                mark.style.width = `${Math.max(8, r.width + 6)}px`;
                mark.style.height = `${Math.max(8, r.height + 6)}px`;
                mark.style.border = `3px solid ${border}`;
                mark.style.background = preferred ? 'rgba(0, 200, 83, 0.12)' : 'rgba(255, 145, 0, 0.1)';
                mark.style.zIndex = '2147483646';
                mark.style.pointerEvents = 'none';
                mark.style.boxSizing = 'border-box';

                const label = document.createElement('div');
                label.id = `__msg_label_${stamp}_${idx}`;
                label.textContent = preferred ? `MSG #${idx + 1} (preferred)` : `MSG #${idx + 1}`;
                label.style.position = 'fixed';
                label.style.left = `${Math.max(0, r.left)}px`;
                label.style.top = `${Math.max(0, r.top - 22)}px`;
                label.style.padding = '2px 6px';
                label.style.background = border;
                label.style.color = '#fff';
                label.style.font = '700 11px/1.2 Arial, sans-serif';
                label.style.zIndex = '2147483647';
                label.style.pointerEvents = 'none';

                document.body.appendChild(mark);
                document.body.appendChild(label);
                marks.push({
                    index: idx + 1,
                    preferred,
                    score: item.score,
                    inMain: item.inMain,
                    inTopCard: item.inTopCard,
                    rect: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) },
                });
            });
            return { marks, preferredIndex: marks.length ? 1 : 0, count: marks.length };
            """
        ) or {}

        debug_dir = _project_root_dir() / "debug_output"
        debug_dir.mkdir(parents=True, exist_ok=True)
        out_path = debug_dir / f"message_preclick_marked_{int(time.time() * 1000)}.png"
        driver.save_screenshot(str(out_path))
        logger.info("message candidates marked screenshot saved=%s meta=%s", out_path, meta)
        return str(out_path), meta if isinstance(meta, dict) else {}
    except Exception as exc:
        logger.info("message marked screenshot failed err=%s", exc)
        return None, {"error": str(exc)}
    finally:
        try:
            driver.execute_script(
                """
                for (const n of Array.from(document.querySelectorAll("[id^='__msg_marker_'], [id^='__msg_label_']"))) {
                    n.remove();
                }
                """
            )
        except Exception:
            pass


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


def _human_pause(min_s: float, max_s: float, *, label: str) -> None:
    lo = max(0.0, min_s)
    hi = max(lo, max_s)
    delay = random.uniform(lo, hi)
    logger.info("human-like pause label=%s delay=%.2fs", label, delay)
    time.sleep(delay)


def _capture_message_button_structure(driver: uc.Chrome) -> dict[str, Any]:
    """Capture visible Message-like button candidates (including shadow roots)."""
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
        const textOf = (el) => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
        const roots = [document];
        const interop = document.getElementById('interop-outlet');
        if (interop) roots.push(interop);
        if (interop && interop.shadowRoot) roots.push(interop.shadowRoot);

        const candidates = [];
        for (const root of roots) {
            const nodes = deepNodes(root, "button, a[role='button'], div[role='button'], a");
            for (const n of nodes) {
                if (!isVisible(n)) continue;
                const txt = textOf(n).toLowerCase();
                const aria = (n.getAttribute('aria-label') || '').toLowerCase();
                if (!(txt === 'message' || txt.startsWith('message ') || aria.includes('message'))) continue;
                const r = n.getBoundingClientRect();
                const owner = n.closest("button, a[role='button'], div[role='button'], a") || n;
                candidates.push({
                    text: textOf(n).slice(0, 120),
                    aria: (n.getAttribute('aria-label') || '').slice(0, 120),
                    ownerText: textOf(owner).slice(0, 120),
                    ownerAria: (owner.getAttribute('aria-label') || '').slice(0, 120),
                    inMain: !!n.closest('main'),
                    inTopCard: !!n.closest('main section'),
                    inMessagingDock: !!n.closest('#msg-overlay, .msg-overlay-list-bubble, .msg-overlay-container'),
                    className: (typeof owner.className === 'string' ? owner.className : '').slice(0, 180),
                    rect: {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)},
                });
                if (candidates.length >= 40) break;
            }
            if (candidates.length >= 40) break;
        }
        return {
            url: window.location.href,
            title: document.title,
            candidates,
            count: candidates.length,
        };
    """
    return driver.execute_script(js) or {}


def _wait_until_document_complete(driver: uc.Chrome, timeout_s: float = 30.0) -> None:
    WebDriverWait(driver, timeout_s).until(
        lambda d: d.execute_script("return document.readyState") == "complete"
    )


def _wait_until_main_profile_shell_visible(driver: uc.Chrome, timeout_s: float = 25.0) -> bool:
    """Wait until the profile main column has rendered (reduces racing LinkedIn hydration)."""
    try:
        WebDriverWait(driver, timeout_s).until(
            lambda d: bool(
                d.execute_script(
                    """
                    const m = document.querySelector('main');
                    if (!m) return false;
                    const r = m.getBoundingClientRect();
                    return r.height > 140;
                    """
                )
            )
        )
        return True
    except TimeoutException:
        logger.info("main profile shell not tall enough in time; continuing")
        return False


def _yield_browser_idle_before_ui(driver: uc.Chrome) -> None:
    """Best-effort: wait for rAF + requestIdleCallback so initial scripts settle before clicks."""
    try:
        driver.set_script_timeout(35)
        driver.execute_async_script(
            """
            const done = arguments[arguments.length - 1];
            const jitter = 140 + Math.floor(Math.random() * 420);
            requestAnimationFrame(() => {
                requestAnimationFrame(() => {
                    setTimeout(() => {
                        if (typeof requestIdleCallback === 'function') {
                            requestIdleCallback(() => done(true), { timeout: 2200 });
                        } else {
                            setTimeout(() => done(true), 480 + Math.floor(Math.random() * 520));
                        }
                    }, jitter);
                });
            });
            """
        )
    except Exception as exc:
        logger.info("yield_browser_idle_before_ui failed err=%s; continuing", exc)


def _find_preferred_message_owner_element(driver: uc.Chrome) -> Any | None:
    """Return the same top-scored Message control as the green overlay (shadow-aware)."""
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
        const textOf = (el) => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
        const roots = [document];
        const interop = document.getElementById('interop-outlet');
        if (interop) roots.push(interop);
        if (interop && interop.shadowRoot) roots.push(interop.shadowRoot);

        const scored = [];
        const seenOwners = new WeakSet();
        for (const root of roots) {
            const nodes = deepNodes(root, "button, a[role='button'], div[role='button'], a");
            for (const n of nodes) {
                if (!isVisible(n)) continue;
                const txt = textOf(n).toLowerCase();
                const aria = (n.getAttribute('aria-label') || '').toLowerCase();
                if (!(txt === 'message' || txt.startsWith('message ') || aria.includes('message'))) continue;
                const owner = n.closest("button, a[role='button'], div[role='button'], a") || n;
                if (seenOwners.has(owner)) continue;
                seenOwners.add(owner);
                const r = owner.getBoundingClientRect();
                const score =
                    (owner.closest('main section') ? 100 : 0) +
                    (owner.closest('main') ? 35 : 0) +
                    (!owner.closest('#msg-overlay, .msg-overlay-list-bubble, .msg-overlay-container') ? 25 : 0) +
                    (r.y >= 40 && r.y < 900 ? 15 : 0);
                scored.push({ owner, score });
            }
        }
        if (!scored.length) return null;
        scored.sort((a, b) => b.score - a.score);
        return scored[0].owner;
    """
    try:
        return driver.execute_script(js)
    except Exception:
        return None


def _click_profile_message_button(driver: uc.Chrome) -> tuple[bool, str | None, list[str]]:
    """Click Message: human-like pointer path first; synthetic DOM .click() only as last resort."""
    strategies: list[str] = []

    # Clear floating messaging bubbles that can intercept clicks on the profile CTA.
    try:
        unblock_meta = driver.execute_script(
            """
            const isVisible = (el) => {
                const r = el.getBoundingClientRect();
                const s = window.getComputedStyle(el);
                return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
            };
            const overlays = Array.from(
                document.querySelectorAll('#msg-overlay, .msg-overlay-container, .msg-overlay-list-bubble, aside[class*="msg-overlay"]')
            ).filter(isVisible);
            let clicked = 0;
            for (const ov of overlays) {
                const controls = Array.from(
                    ov.querySelectorAll("button, [role='button'], a[role='button']")
                ).filter(isVisible);
                // Prefer close/minimize controls in header.
                const target = controls.find((b) => {
                    const aria = (b.getAttribute('aria-label') || '').toLowerCase();
                    const title = (b.getAttribute('title') || '').toLowerCase();
                    const cls = (b.className || '').toLowerCase();
                    return (
                        aria.includes('close') ||
                        aria.includes('dismiss') ||
                        aria.includes('minimize') ||
                        title.includes('close') ||
                        title.includes('dismiss') ||
                        title.includes('minimize') ||
                        cls.includes('msg-overlay-bubble-header__control')
                    );
                });
                if (!target) continue;
                try {
                    target.scrollIntoView({block:'nearest'});
                    target.click();
                    clicked += 1;
                } catch (_) {}
            }
            return {overlayCount: overlays.length, controlsClicked: clicked};
            """
        ) or {}
        strategies.append(
            f"ui_unblock_overlays={unblock_meta.get('overlayCount', 0)}_controls_clicked={unblock_meta.get('controlsClicked', 0)}"
        )
        if int(unblock_meta.get("controlsClicked", 0)) > 0:
            _human_pause(0.25, 0.75, label="after_ui_unblock_before_message_click")
    except Exception as exc:
        logger.info("message pre-click ui_unblock failed err=%s", exc)
        strategies.append(f"ui_unblock_exception:{exc!s}")

    _human_pause(1.0, 2.3, label="before_message_button_click")
    strategies.append("random_pause_before_click")

    preferred = _find_preferred_message_owner_element(driver)
    if preferred is not None:
        strategies.append("preferred_resolved_shadow_aware_scoring_matches_green_overlay")
        if _human_like_click(driver, preferred, label="message_profile_preferred"):
            strategies.append("human_like_click_primary")
            logger.info("message click success via human_like_click on preferred element")
            return True, "human_like_preferred", strategies
        strategies.append("human_like_click_exhausted")

    # Fallback: full pointer + native click in page context (no WebElement), same as earlier.
    js_fallback = """
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
        const textOf = (el) => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim().toLowerCase();
        const roots = [document];
        const interop = document.getElementById('interop-outlet');
        if (interop) roots.push(interop);
        if (interop && interop.shadowRoot) roots.push(interop.shadowRoot);
        const scored = [];
        const seenOwners = new WeakSet();
        for (const root of roots) {
            const nodes = deepNodes(root, "button, a[role='button'], div[role='button'], a");
            for (const n of nodes) {
                if (!isVisible(n)) continue;
                const txt = textOf(n);
                const aria = (n.getAttribute('aria-label') || '').toLowerCase();
                if (!(txt === 'message' || txt.startsWith('message ') || aria.includes('message'))) continue;
                const owner = n.closest("button, a[role='button'], div[role='button'], a") || n;
                if (seenOwners.has(owner)) continue;
                seenOwners.add(owner);
                const r = owner.getBoundingClientRect();
                const score =
                    (owner.closest('main section') ? 100 : 0) +
                    (owner.closest('main') ? 35 : 0) +
                    (!owner.closest('#msg-overlay, .msg-overlay-list-bubble, .msg-overlay-container') ? 25 : 0) +
                    (r.y >= 40 && r.y < 900 ? 15 : 0);
                scored.push({ owner, score });
            }
        }
        if (!scored.length) return {clicked:false, reason:'message_button_not_found'};
        scored.sort((a, b) => b.score - a.score);
        const target = scored[0].owner;
        target.scrollIntoView({block:'center'});
        target.focus();
        const r = target.getBoundingClientRect();
        const cx = Math.floor(r.left + r.width / 2);
        const cy = Math.floor(r.top + r.height / 2);
        const events = ['pointerover','mouseover','pointerenter','mouseenter','pointermove','mousemove','pointerdown','mousedown','pointerup','mouseup','click'];
        for (const ev of events) {
            const Evt = ev.startsWith('pointer') ? PointerEvent : MouseEvent;
            target.dispatchEvent(new Evt(ev, {bubbles:true, cancelable:true, composed:true, clientX:cx, clientY:cy, pointerType:'mouse'}));
        }
        try { target.click(); } catch (_) {}
        return {clicked:true, reason:'js_pointer_native_click_preferred'};
    """
    res: dict[str, Any] = {}
    try:
        res = driver.execute_script(js_fallback) or {}
        logger.info("message click fallback result=%s", res)
        strategies.append("execute_script_pointer_events_plus_native_click_on_preferred_owner")
        if bool(res.get("clicked")):
            return True, str(res.get("reason") or "js_fallback"), strategies
    except Exception as exc:
        logger.info("message click js_fallback failed err=%s", exc)
        strategies.append(f"js_fallback_exception:{exc!s}")

    # Last resort: HTMLElement.click() on the WebElement — bypasses z-order but looks less human.
    if preferred is not None:
        try:
            clicked_dom = driver.execute_script(
                """
                const el = arguments[0];
                if (!el) return false;
                el.scrollIntoView({block: 'center'});
                try { el.focus(); } catch (e) {}
                el.click();
                return true;
                """,
                preferred,
            )
            if clicked_dom:
                strategies.append("last_resort_direct_dom_click_bypasses_stacking_order")
                logger.info("message click last resort: HTMLElement.click() on preferred")
                return True, "dom_click_preferred_last_resort", strategies
        except Exception as exc:
            strategies.append(f"last_resort_dom_click_failed:{exc!s}")

    return False, str(res.get("reason") or ""), strategies


def _find_message_composer_webelement(driver: uc.Chrome) -> Any | None:
    """Resolve the message composer (textarea or contenteditable) using the same scoring as UI hints."""
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
        const matchesWriteMessageHint = (el) => {
            const ph = (el.getAttribute('placeholder') || '').toLowerCase();
            const al = (el.getAttribute('aria-label') || '').toLowerCase();
            const dp = (el.getAttribute('data-placeholder') || '').toLowerCase();
            const hint = ph || al || dp;
            return hint.includes('write') && hint.includes('message');
        };
        const inMessagingUi = (el) => !!el.closest(
            '#msg-overlay, .msg-overlay-container, .msg-form, .msg-overlay-list-bubble, [class*="msg-overlay"], [data-msg-overlay]'
        );
        const roots = [document];
        const interop = document.getElementById('interop-outlet');
        if (interop) { roots.push(interop); if (interop.shadowRoot) roots.push(interop.shadowRoot); }
        for (const el of document.querySelectorAll('#msg-overlay, .msg-overlay-container')) {
            roots.push(el);
            if (el.shadowRoot) roots.push(el.shadowRoot);
        }
        const seen = new WeakSet();
        const allEditors = [];
        for (const sel of ['textarea', "div[role='textbox'][contenteditable='true']", "div[contenteditable='true']", '.msg-form__contenteditable']) {
            for (const root of roots) {
                for (const n of deepNodes(root, sel)) {
                    if (seen.has(n) || !isVisible(n)) continue;
                    seen.add(n);
                    allEditors.push(n);
                }
            }
        }
        const score = (el) => {
            let s = 0;
            const r = el.getBoundingClientRect();
            if (matchesWriteMessageHint(el)) s += 500;
            if (inMessagingUi(el)) s += 200;
            if (el.tagName === 'TEXTAREA') s += 80;
            if (el.classList && el.classList.contains('msg-form__contenteditable')) s += 120;
            if (el.getAttribute && el.getAttribute('role') === 'textbox') s += 40;
            s += Math.min(60, (r.width * r.height) / 2000);
            return s;
        };
        allEditors.sort((a, b) => score(b) - score(a));
        return allEditors[0] || null;
    """
    try:
        return driver.execute_script(js)
    except Exception:
        return None


def _is_message_composer_open(driver: uc.Chrome) -> bool:
    """True if the messaging composer is visible (shadow-aware, matches LinkedIn DOM)."""
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
        const matchesWriteMessageHint = (el) => {
            const ph = (el.getAttribute('placeholder') || '').toLowerCase();
            const al = (el.getAttribute('aria-label') || '').toLowerCase();
            const dp = (el.getAttribute('data-placeholder') || '').toLowerCase();
            const hint = ph || al || dp;
            return hint.includes('write') && hint.includes('message');
        };
        const roots = [document];
        const interop = document.getElementById('interop-outlet');
        if (interop) { roots.push(interop); if (interop.shadowRoot) roots.push(interop.shadowRoot); }
        const extra = document.querySelectorAll('#msg-overlay, .msg-overlay-container, aside[class*="msg-overlay"]');
        for (const el of extra) {
            roots.push(el);
            if (el.shadowRoot) roots.push(el.shadowRoot);
        }
        const seen = new WeakSet();
        const collect = (selector) => roots.flatMap((r) => deepNodes(r, selector)).filter((n) => {
            if (seen.has(n)) return false;
            seen.add(n);
            return isVisible(n);
        });
        if (collect('textarea').some(matchesWriteMessageHint)) return true;
        if (collect('textarea').some((el) => !!el.closest('#msg-overlay, .msg-overlay-container, .msg-form, [class*="msg-overlay"]')))
            return true;
        for (const el of collect("div[role='textbox'][contenteditable='true'], div[contenteditable='true']")) {
            if (matchesWriteMessageHint(el)) return true;
            if (el.classList && el.classList.contains('msg-form__contenteditable')) return true;
        }
        if (collect('.msg-form__contenteditable').length) return true;
        if (collect("section.msg-overlay-bubble-header, div.msg-overlay-bubble-header").length) return true;
        return false;
    """
    try:
        return bool(driver.execute_script(js))
    except Exception:
        return False


def _find_message_modal_close_button(driver: uc.Chrome) -> Any | None:
    """Header dismiss/close control on the floating message overlay (shadow-aware)."""
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
        if (interop) { roots.push(interop); if (interop.shadowRoot) roots.push(interop.shadowRoot); }
        for (const el of document.querySelectorAll('#msg-overlay, .msg-overlay-container, .msg-overlay-list-bubble')) {
            roots.push(el);
            if (el.shadowRoot) roots.push(el.shadowRoot);
        }
        const scored = [];
        const seen = new WeakSet();
        for (const root of roots) {
            for (const btn of deepNodes(root, 'button')) {
                if (seen.has(btn) || !isVisible(btn)) continue;
                const overlay = btn.closest(
                    '#msg-overlay, .msg-overlay-container, .msg-overlay-list-bubble, ' +
                    '[class*="msg-overlay"], section.msg-overlay-bubble-header, .msg-overlay-bubble-header'
                );
                if (!overlay) continue;
                const aria = (btn.getAttribute('aria-label') || '').toLowerCase();
                const title = (btn.getAttribute('title') || '').toLowerCase();
                const cls = (btn.className || '').toLowerCase();
                const iconUse = btn.querySelector('use');
                const iconHref = ((iconUse && (iconUse.getAttribute('href') || iconUse.getAttribute('xlink:href'))) || '').toLowerCase();
                const iconData = (
                    (btn.querySelector('[data-test-icon]') && btn.querySelector('[data-test-icon]').getAttribute('data-test-icon')) ||
                    ''
                ).toLowerCase();
                if (aria.includes('minimize') || aria.includes('expand')) continue;
                if (aria.includes('send')) continue;
                const looksClose =
                    aria.includes('dismiss') ||
                    (aria.includes('close') && !aria.includes('send')) ||
                    title.includes('close') ||
                    title.includes('dismiss') ||
                    cls.includes('msg-overlay-bubble-header__control') ||
                    cls.includes('artdeco-button--muted') ||
                    iconHref.includes('close') ||
                    iconData.includes('close');
                if (!looksClose) continue;
                seen.add(btn);
                const r = btn.getBoundingClientRect();
                let s = 0;
                if (aria.includes('dismiss')) s += 150;
                if (btn.closest('header, .msg-overlay-bubble-header')) s += 60;
                if (cls.includes('msg-overlay-bubble-header__control')) s += 80;
                if (iconHref.includes('close') || iconData.includes('close')) s += 120;
                s += Math.max(0, 80 - r.top / 8);
                if (r.right > window.innerWidth - 100) s += 35;
                scored.push({ btn, s });
            }
        }
        scored.sort((a, b) => b.s - a.s);
        return scored.length ? scored[0].btn : null;
    """
    try:
        return driver.execute_script(js)
    except Exception:
        return None


def _close_message_modal_after_send(driver: uc.Chrome) -> tuple[bool, str | None]:
    """Click overlay dismiss (X) after send so the chat panel closes."""
    close_attempts = (
        ("human_like_close", lambda el: _human_like_click(driver, el, label="message_modal_close")),
        ("mouse_sequence_close", lambda el: _dispatch_mouse_sequence_js(driver, el)),
        (
            "script_click_close",
            lambda el: bool(
                driver.execute_script(
                    "const n=arguments[0]; if(!n) return false; n.scrollIntoView({block:'nearest'}); n.click(); return true;",
                    el,
                )
            ),
        ),
    )
    for attempt in range(3):
        close_btn = _find_message_modal_close_button(driver)
        if close_btn is None:
            logger.info("message modal close button not found attempt=%s", attempt + 1)
            continue
        _human_pause(0.35, 0.95, label="before_message_modal_close_click")
        for source, clicker in close_attempts:
            try:
                if clicker(close_btn):
                    logger.info("message modal close clicked via=%s attempt=%s", source, attempt + 1)
                    # Let LinkedIn animate and remove/minimize the panel.
                    _human_pause(0.25, 0.65, label="after_message_modal_close_click")
                    still_open = bool(
                        driver.execute_script(
                            """
                            const isVisible = (el) => {
                                const r = el.getBoundingClientRect();
                                const s = window.getComputedStyle(el);
                                return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
                            };
                            const overlays = Array.from(
                                document.querySelectorAll(
                                    '#msg-overlay, .msg-overlay-container, .msg-overlay-list-bubble, aside[class*="msg-overlay"]'
                                )
                            ).filter(isVisible);
                            return overlays.length > 0;
                            """
                        )
                    )
                    if not still_open:
                        logger.info("message modal closed successfully source=%s", source)
                        return True, source
            except Exception as exc:
                logger.info("message modal close failed source=%s err=%s", source, exc)
                continue
    logger.info("message modal did not close after retries")
    return False, None


def _fill_and_send_message(driver: uc.Chrome, message_text: str) -> tuple[bool, bool]:
    """Focus composer with human-like click (like connection note flow), then char-by-char typing, then Send."""
    js_send = """
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
        if (interop) { roots.push(interop); if (interop.shadowRoot) roots.push(interop.shadowRoot); }
        for (const el of document.querySelectorAll('#msg-overlay, .msg-overlay-container')) {
            roots.push(el);
            if (el.shadowRoot) roots.push(el.shadowRoot);
        }
        const candidates = [];
        const seen = new WeakSet();
        for (const root of roots) {
            for (const b of deepNodes(root, "button, div[role='button'], span[role='button']")) {
                if (seen.has(b) || !isVisible(b)) continue;
                seen.add(b);
                const txt = (b.innerText || b.textContent || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                const aria = (b.getAttribute('aria-label') || '').trim().toLowerCase();
                const isSend =
                    txt === 'send' ||
                    aria === 'send' ||
                    aria.includes('send message') ||
                    (aria.includes('send') && !aria.includes('invitation'));
                if (!isSend) continue;
                let s = 0;
                if (b.closest('#msg-overlay, .msg-overlay-container, .msg-form, [class*="msg-overlay"]')) s += 100;
                if (!b.disabled && b.getAttribute('aria-disabled') !== 'true') s += 50;
                candidates.push({b, s});
            }
        }
        candidates.sort((a, b) => b.s - a.s);
        const pick = candidates[0];
        if (!pick) return {sent:false, reason:'send_not_found'};
        const btn = pick.b;
        if (btn.disabled || btn.getAttribute('aria-disabled') === 'true') return {sent:false, reason:'send_disabled'};
        btn.scrollIntoView({block:'center'});
        btn.focus();
        btn.click();
        return {sent:true, reason:'send_clicked'};
    """
    try:
        composer = _find_message_composer_webelement(driver)
        if composer is None:
            logger.info("message composer element not found")
            return False, False

        focused = _human_like_click(driver, composer, label="message_composer_focus")
        if not focused:
            if _dispatch_mouse_sequence_js(driver, composer):
                focused = True
                logger.info("composer focus via dispatch_mouse_sequence_js")
        if not focused:
            try:
                driver.execute_script(
                    "const el=arguments[0]; if(el){el.scrollIntoView({block:'center'}); el.focus(); el.click();}",
                    composer,
                )
                focused = True
                logger.info("composer focus via execute_script click fallback")
            except Exception as exc:
                logger.info("composer focus click failed err=%s", exc)
                return False, False

        _human_pause(0.28, 0.75, label="after_composer_focus_click")

        try:
            tag = (composer.tag_name or "").lower()
        except Exception:
            tag = ""
        if tag == "textarea":
            try:
                composer.clear()
            except Exception:
                pass
        else:
            try:
                driver.execute_script(
                    "const el=arguments[0]; if(el){el.focus(); el.textContent=''; el.innerHTML='';}",
                    composer,
                )
            except Exception:
                pass

        filled_ok = False
        try:
            _type_text_human_like_webelement(composer, message_text)
            filled_ok = True
            logger.info(
                "message body typed via _type_text_human_like_webelement chars=%s", len(message_text)
            )
        except Exception as exc:
            logger.info("human-like send_keys typing failed err=%s; trying JS fill", exc)

        if not filled_ok:
            try:
                driver.execute_script(
                    """
                    const el = arguments[0];
                    const text = arguments[1];
                    if (!el) return false;
                    el.focus();
                    if (el.tagName === 'TEXTAREA') {
                        el.value = text;
                        el.dispatchEvent(new Event('input', {bubbles:true}));
                        el.dispatchEvent(new Event('change', {bubbles:true}));
                    } else {
                        el.innerHTML = '';
                        el.textContent = text;
                        el.dispatchEvent(new InputEvent('input', {
                            bubbles:true, cancelable:true, inputType:'insertText', data:text
                        }));
                    }
                    return true;
                    """,
                    composer,
                    message_text,
                )
                filled_ok = True
                logger.info("message body set via JS fallback")
            except Exception as exc2:
                logger.info("JS message fill fallback failed err=%s", exc2)
                return False, False

        _human_pause(0.55, 1.35, label="after_message_body_typed")
        sent = False
        last_send: dict[str, Any] = {}
        for attempt in range(36):
            last_send = driver.execute_script(js_send) or {}
            if bool(last_send.get("sent")):
                sent = True
                logger.info("message send click result=%s attempt=%s", last_send, attempt)
                break
            if last_send.get("reason") == "send_not_found":
                logger.info("message send not found yet attempt=%s", attempt)
            else:
                logger.info("message send retry reason=%s attempt=%s", last_send.get("reason"), attempt)
            time.sleep(0.25)
        if not sent:
            logger.info("message send gave up after retries last=%s", last_send)
        return True, sent
    except Exception as exc:
        logger.info("message compose/send js failed err=%s", exc)
        return False, False


def _send_message_with_driver(
    driver: uc.Chrome,
    profile_url: str,
    message_text: str,
    *,
    initial_wait_s: float = 4.0,
    timeout_s: int = 25,
    diagnose_only: bool = False,
    debug: bool = False,
) -> dict[str, Any]:
    """Core message-send logic on an already-open browser (no launch/quit)."""
    result: dict[str, Any] = {
        "profile_url": profile_url,
        "message_button_clicked": False,
        "message_composer_opened": False,
        "message_filled": False,
        "message_sent": False,
        "message_modal_closed": False,
    }

    driver.get(profile_url)
    time.sleep(max(0.0, initial_wait_s))
    try:
        _wait_until_document_complete(driver)
    except TimeoutException:
        logger.info("document.readyState did not reach complete in time; continuing")
    shell_ok = _wait_until_main_profile_shell_visible(driver)
    result["profile_main_shell_ready"] = shell_ok
    _yield_browser_idle_before_ui(driver)
    settle_lo = _HUMAN_IDLE_AFTER_SCRIPTS_MIN_S
    settle_hi = _HUMAN_IDLE_AFTER_SCRIPTS_MAX_S
    _human_pause(settle_lo, settle_hi, label="human_idle_after_scripts_before_ui")
    _human_pause(1.0, 2.5, label="after_profile_load")
    result["current_url"] = driver.current_url
    result["page_title"] = driver.title

    if debug:
        structure_before = _capture_message_button_structure(driver)
        result["message_button_structure_before_click"] = structure_before
        logger.info("message structure before click count=%s", structure_before.get("count"))

        marked_path, overlay_meta = _capture_message_candidates_marked_screenshot(driver)
        result["message_preclick_marked_screenshot"] = marked_path
        result["message_preclick_overlay_meta"] = overlay_meta

    if diagnose_only:
        return result

    if _is_message_composer_open(driver):
        logger.info(
            "composer already visible (e.g. open chat overlay); skipping profile Message click"
        )
        result["skipped_profile_message_button"] = True
        result["message_button_clicked"] = True
        result["message_click_source"] = "skipped_profile_click_composer_already_visible"
        result["message_click_strategies"] = ["composer_detected_before_click"]
        result["message_composer_opened"] = True
    else:
        clicked, source, click_strategies = _click_profile_message_button(driver)
        result["message_button_clicked"] = clicked
        result["message_click_source"] = source
        result["message_click_strategies"] = click_strategies
        if not clicked:
            _human_pause(0.6, 1.2, label="after_failed_message_click_recheck")
            if _is_message_composer_open(driver):
                logger.info(
                    "Message click reported failure but composer is visible; continuing"
                )
                result["message_button_clicked"] = True
                result["message_click_source"] = (
                    f"{source}|composer_visible_after_failed_click"
                    if source
                    else "composer_visible_after_failed_click"
                )
                result["message_composer_opened"] = True
            else:
                return result
        else:
            _human_pause(1.2, 2.8, label="after_message_click")
            try:
                WebDriverWait(driver, timeout_s).until(
                    lambda d: _is_message_composer_open(d)
                )
                result["message_composer_opened"] = True
            except TimeoutException:
                logger.info(
                    "composer did not appear within %ss after Message click", timeout_s
                )
                result["message_composer_opened"] = False
                result["composer_wait_timeout_s"] = timeout_s
                return result

    _human_pause(0.8, 1.8, label="before_message_fill")
    filled, sent = _fill_and_send_message(driver, message_text)
    result["message_filled"] = filled
    result["message_sent"] = sent
    if sent:
        closed, close_src = _close_message_modal_after_send(driver)
        result["message_modal_closed"] = closed
        if close_src:
            result["message_modal_close_source"] = close_src
    return result


def send_linkedin_message_sync(
    profile_url: str,
    *,
    message_text: str,
    storage_state_path: str | Path = DEFAULT_STORAGE_PATH,
    headless: bool = False,
    initial_wait_s: float = 4.0,
    timeout_s: int = 25,
    wait_before_close_s: float = 15.0,
    diagnose_only: bool = False,
    debug: bool = False,
) -> dict[str, Any]:
    """Open profile, inspect Message structure, click Message, compose and send."""
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

    logger.info("message sender launch headless=%s version_main=%s", headless, version_main)
    driver = uc.Chrome(**chrome_kwargs)
    try:
        injected = _inject_linkedin_cookies(driver, storage_data)
        logger.info("cookies injected=%s", injected)
        result = _send_message_with_driver(
            driver,
            profile_url,
            message_text,
            initial_wait_s=initial_wait_s,
            timeout_s=timeout_s,
            diagnose_only=diagnose_only,
            debug=debug,
        )
        result["cookie_count_injected"] = injected
        return result
    finally:
        if wait_before_close_s > 0:
            logger.info("waiting %.1fs before closing browser", wait_before_close_s)
            time.sleep(wait_before_close_s)
        driver.quit()

