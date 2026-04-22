import logging
import time
from typing import Optional
import undetected_chromedriver as uc
from selenium.webdriver.support.ui import WebDriverWait

logger = logging.getLogger(__name__)

def resolve_recruiter_profile_url(
    driver: uc.Chrome,
    thread_click_fn,
    expected_profile_name: str = "",
) -> Optional[str]:
    """
    Robustly resolves a recruiter's canonical profile URL by:
    1. Clicking the chat thread.
    2. Clicking the sender's name link in the chat header.
    3. Extracting the URL from the final loaded profile page.
    """
    # 1. Click the thread to open details (passed as a lambda or handled here)
    if not thread_click_fn():
        logger.error("Failed to click thread for resolution.")
        return None

    time.sleep(1.5)  # Wait for bubble hydration

    # 2) Primary strategy requested: click the header text shown right of avatar.
    clicked_name = bool(
        driver.execute_script(
            """
            const expectedNameRaw = (arguments[0] || '').trim();
            const normalize = (s) => (s || '').toLowerCase().replace(/\\s+/g, ' ').trim();
            const expected = normalize(expectedNameRaw);
            const isVisible = (el) => {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                const st = window.getComputedStyle(el);
                return r.width > 0 && r.height > 0 && st.display !== 'none' && st.visibility !== 'hidden';
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
            for (const el of document.querySelectorAll('#msg-overlay, .msg-overlay-container, .msg-overlay-list-bubble, aside[class*="msg-overlay"]')) {
                roots.push(el);
                if (el.shadowRoot) roots.push(el.shadowRoot);
            }

            // Focus only on active message detail header and the name region left of close button.
            const isCloseBtn = (el) => {
                if (!el) return false;
                const aria = (el.getAttribute('aria-label') || '').toLowerCase();
                const title = (el.getAttribute('title') || '').toLowerCase();
                const cls = (el.className || '').toLowerCase();
                return (
                    aria.includes('close') ||
                    aria.includes('dismiss') ||
                    title.includes('close') ||
                    title.includes('dismiss') ||
                    cls.includes('msg-overlay-bubble-header__control')
                );
            };
            const headers = [];
            const seen = new WeakSet();
            for (const root of roots) {
                for (const h of deepNodes(root, '.msg-overlay-bubble-header, .msg-thread-header')) {
                    if (!h || seen.has(h) || !isVisible(h)) continue;
                    seen.add(h);
                    const closeBtns = Array.from(h.querySelectorAll('button, [role="button"], a[role="button"]')).filter(isCloseBtn);
                    if (!closeBtns.length) continue;
                    headers.push({ h, closeBtns });
                }
            }
            if (!headers.length) return false;

            // Prefer the right-most visible header (active detail panel near compose box).
            headers.sort((a, b) => {
                const ar = a.h.getBoundingClientRect();
                const br = b.h.getBoundingClientRect();
                const ax = ar.left + ar.width;
                const bx = br.left + br.width;
                if (bx !== ax) return bx - ax;
                return br.top - ar.top;
            });
            const chosenHeader = headers[0].h;
            const closeBtn = headers[0].closeBtns.sort((a, b) => b.getBoundingClientRect().right - a.getBoundingClientRect().right)[0];
            if (!closeBtn) return false;

            const closeRect = closeBtn.getBoundingClientRect();
            const headerRect = chosenHeader.getBoundingClientRect();
            const clickBandLeft = headerRect.left;
            const clickBandRight = Math.max(headerRect.left + 20, closeRect.left - 10); // strictly left of X

            const candidates = [];
            const nodes = chosenHeader.querySelectorAll('a, span, div, strong, h1, h2, h3, [role="button"]');
            for (const n of Array.from(nodes)) {
                if (!isVisible(n)) continue;
                if (n === closeBtn || n.contains(closeBtn)) continue;
                const txt = normalize(n.innerText || n.textContent || '');
                if (!txt || txt.length < 2) continue;
                const r = n.getBoundingClientRect();
                const cx = r.left + (r.width / 2);
                const inLeftBand = cx >= clickBandLeft && cx <= clickBandRight;
                if (!inLeftBand) continue;
                let score = 0;
                if (expected && txt.includes(expected)) score += 140;
                if (n.matches('a,[role="button"]')) score += 25;
                if (txt.split(' ').length <= 4) score += 10;
                score += Math.max(0, 20 - Math.abs((closeRect.left - 20) - cx) / 15); // close to text-left-of-X region
                candidates.push({ n, score });
            }
            if (!candidates.length) return false;
            candidates.sort((a, b) => b.score - a.score);
            const pick = candidates[0].n;
            const target = pick.closest('a,[role="button"],button') || pick;
            try { target.scrollIntoView({ block: 'center', inline: 'nearest' }); } catch (_) {}
            try { target.click(); return true; } catch (_) {}
            try {
                const r = target.getBoundingClientRect();
                const cx = Math.floor(r.left + r.width / 2);
                const cy = Math.floor(r.top + r.height / 2);
                const p = document.elementFromPoint(cx, cy) || target;
                for (const ev of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                    const Evt = ev.startsWith('pointer') ? PointerEvent : MouseEvent;
                    p.dispatchEvent(new Evt(ev, { bubbles: true, cancelable: true, composed: true, clientX: cx, clientY: cy, pointerType: 'mouse' }));
                }
                return true;
            } catch (_) {}
            return false;
            """,
            expected_profile_name,
        )
    )

    if clicked_name:
        try:
            WebDriverWait(driver, 20).until(
                lambda d: "/in/" in (d.current_url or "")
            )
            time.sleep(1.0)
            final_url = driver.current_url
            logger.info("Deep Resolve: profile captured from same tab after header-name click url=%s", final_url)
            if "/in/" in final_url:
                return final_url
        except Exception:
            # If same-tab navigation did not occur, continue fallback strategies.
            pass

    # 3) Fallback: find href in header and open in new tab.
    js_find_header_link = """
    const expectedNameRaw = (arguments[0] || '').trim();
    const normalize = (s) => (s || '').toLowerCase().replace(/\\s+/g, ' ').trim();
    const expected = normalize(expectedNameRaw);
    const isVisible = (el) => {
        if (!el) return false;
        const r = el.getBoundingClientRect();
        const st = window.getComputedStyle(el);
        return r.width > 0 && r.height > 0 && st.display !== 'none' && st.visibility !== 'hidden';
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
    for (const el of document.querySelectorAll('#msg-overlay, .msg-overlay-container, .msg-overlay-list-bubble, aside[class*="msg-overlay"]')) {
        roots.push(el);
        if (el.shadowRoot) roots.push(el.shadowRoot);
    }
    const isProfileHref = (href) => {
        if (!href) return false;
        const h = href.toLowerCase();
        return h.includes('/linkedin.com/in/') || h.includes('/www.linkedin.com/in/') || h.includes('/in/');
    };
    const scoreCandidate = (anchor) => {
        let score = 0;
        const href = anchor.getAttribute('href') || '';
        const text = normalize(anchor.innerText || anchor.textContent || '');
        if (href.includes('/in/')) score += 40;
        if (text.length > 1) score += 5;
        if (expected && text.includes(expected)) score += 60;
        if (expected && anchor.closest(`*[aria-label*="${expectedNameRaw}" i]`)) score += 15;
        if (anchor.closest('.msg-overlay-bubble-header, .msg-overlay-bubble-header__details, .msg-thread-header, .msg-thread__link-to-profile')) score += 25;
        return score;
    };

    const panelSelectors = [
        '.msg-overlay-list-bubble',
        '.msg-overlay-conversation-bubble',
        '.msg-overlay-conversation-bubble--is-active',
        '.msg-thread',
        '.msg-s-message-list',
        '.msg-s-message-list-content'
    ];
    const panels = [];
    const seen = new WeakSet();
    for (const root of roots) {
        for (const sel of panelSelectors) {
            for (const node of deepNodes(root, sel)) {
                if (!node || seen.has(node) || !isVisible(node)) continue;
                seen.add(node);
                panels.push(node);
            }
        }
    }

    const candidates = [];
    for (const panel of panels) {
        const links = panel.querySelectorAll(
            '.msg-overlay-bubble-header a[href*="/in/"], ' +
            '.msg-overlay-bubble-header__details a[href*="/in/"], ' +
            '.msg-thread-header a[href*="/in/"], ' +
            '.msg-thread__link-to-profile a[href*="/in/"], ' +
            'a[href*="/in/"]'
        );
        for (const a of Array.from(links)) {
            if (!isVisible(a)) continue;
            const href = a.href || a.getAttribute('href') || '';
            if (!isProfileHref(href)) continue;
            candidates.push(a);
        }
    }

    // Fallback: try globally visible profile links only if panel search found nothing.
    if (!candidates.length) {
        for (const root of roots) {
            const global = deepNodes(root, 'a[href*="/in/"]');
            for (const a of Array.from(global)) {
                if (!isVisible(a)) continue;
                if (!a.closest('.msg-overlay, .msg-overlay-list-bubble, .msg-thread, .msg-s-message-list')) continue;
                const href = a.href || a.getAttribute('href') || '';
                if (!isProfileHref(href)) continue;
                candidates.push(a);
            }
        }
    }

    if (candidates.length) {
        candidates.sort((a, b) => scoreCandidate(b) - scoreCandidate(a));
        const chosen = candidates[0];
        return chosen ? (chosen.href || chosen.getAttribute('href') || null) : null;
    }

    // Final fallback: capture clickable header "name" element when href is not directly exposed.
    const nameCandidates = [];
    const scoreName = (el) => {
        const txt = normalize(el.innerText || el.textContent || '');
        if (!txt) return -1;
        let s = 0;
        if (expected && txt.includes(expected)) s += 70;
        if (el.closest('.msg-overlay-bubble-header, .msg-overlay-bubble-header__details, .msg-thread-header')) s += 30;
        if (el.closest('button, a, [role="button"]')) s += 15;
        return s;
    };
    for (const root of roots) {
        const nodes = deepNodes(
            root,
            '.msg-overlay-bubble-header *,' +
            '.msg-overlay-bubble-header__details *,' +
            '.msg-thread-header *,' +
            '.msg-entity-lockup__entity-title *'
        );
        for (const n of nodes) {
            if (!isVisible(n)) continue;
            const s = scoreName(n);
            if (s <= 0) continue;
            const owner = n.closest('a, button, [role="button"], span, div') || n;
            nameCandidates.push({ owner, s });
        }
    }
    if (!nameCandidates.length) return null;
    nameCandidates.sort((a, b) => b.s - a.s);
    const best = nameCandidates[0].owner;
    if (!best) return null;
    return {
        click_fallback: true,
        text: (best.innerText || best.textContent || '').trim(),
    };
    """

    header_href = driver.execute_script(js_find_header_link, expected_profile_name)
    if not header_href:
        logger.error("Could not find profile link in chat header.")
        return None
    if isinstance(header_href, dict) and header_href.get("click_fallback"):
        logger.info("Deep Resolve: direct href unavailable; clicking header name fallback.")
        clicked = driver.execute_script(
            """
            const expectedNameRaw = (arguments[0] || '').trim();
            const normalize = (s) => (s || '').toLowerCase().replace(/\\s+/g, ' ').trim();
            const expected = normalize(expectedNameRaw);
            const isVisible = (el) => {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                const st = window.getComputedStyle(el);
                return r.width > 0 && r.height > 0 && st.display !== 'none' && st.visibility !== 'hidden';
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
                '.msg-overlay-bubble-header a, .msg-overlay-bubble-header button, .msg-overlay-bubble-header [role="button"]',
                '.msg-overlay-bubble-header__details a, .msg-overlay-bubble-header__details button, .msg-overlay-bubble-header__details [role="button"]',
                '.msg-thread-header a, .msg-thread-header button, .msg-thread-header [role="button"]',
                '.msg-entity-lockup__entity-title a, .msg-entity-lockup__entity-title button, .msg-entity-lockup__entity-title [role="button"]'
            ];
            const candidates = [];
            for (const root of roots) {
                for (const sel of selectors) {
                    for (const el of deepNodes(root, sel)) {
                        if (!isVisible(el)) continue;
                        const txt = normalize(el.innerText || el.textContent || '');
                        if (!txt) continue;
                        let score = 0;
                        if (expected && txt.includes(expected)) score += 90;
                        if (el.closest('.msg-overlay-bubble-header, .msg-thread-header')) score += 20;
                        candidates.push({ el, score });
                    }
                }
            }
            if (!candidates.length) return false;
            candidates.sort((a, b) => b.score - a.score);
            const target = candidates[0].el;
            try { target.scrollIntoView({block:'center'}); } catch (_) {}
            try { target.click(); return true; } catch (_) {}
            return false;
            """,
            expected_profile_name,
        )
        if not clicked:
            logger.error("Deep Resolve: failed to click header-name fallback.")
            return None
        # If click navigated same-tab to profile URL, capture that URL directly.
        try:
            WebDriverWait(driver, 15).until(
                lambda d: "/in/" in (d.current_url or "")
            )
            current = driver.current_url
            if "/in/" in current:
                logger.info("Deep Resolve: captured same-tab profile URL=%s", current)
                return current
        except Exception:
            pass
        # If click opened a new tab itself, handle below by treating as missing href.
        header_href = None

    # 4. Open extracted href and wait for redirect to canonical profile URL.
    try:
        main_window = driver.current_window_handle
        if header_href:
            href = str(header_href).strip()
            if href.startswith("/"):
                href = f"https://www.linkedin.com{href}"
            
            logger.info(f"Deep Resolve: Opening profile link natively via webdriver switch_to.new_window: {href}")
            
            # Bypass all JavaScript popup blockers by using Selenium 4 native webdriver API
            driver.switch_to.new_window('tab')
            driver.get(href)

        # Wait for page load and redirect settling.
        WebDriverWait(driver, 25).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
        WebDriverWait(driver, 25).until(
            lambda d: bool(d.current_url and d.current_url != "about:blank")
        )
        time.sleep(2.0)

        final_url = driver.current_url
        logger.info(f"Deep Resolve: Extracted canonical URL: {final_url}")
        
        # Teardown the new tab and switch back to messaging feed
        if header_href:
            driver.close()
            driver.switch_to.window(main_window)
        
        return final_url

    except Exception as exc:
        logger.exception(f"Deep Resolve failed for {header_href}: {exc}")
        return header_href  # Fallback to the link we found
