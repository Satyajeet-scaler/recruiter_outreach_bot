#!/usr/bin/env python3
"""Manual smoke test for LinkedIn floating messaging widget detection.

Flow:
1) Open LinkedIn signup page.
2) Restore logged-in session from linkedin_storage.json.
3) Open feed/home and wait extra time for late widget hydration.
4) Detect and mark floating messaging widget.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from dataclasses import asdict

from services.linkedin_inbox import inbox_scraper


def _collect_dom_marker_counts(driver) -> dict[str, int]:
    """Collect selector counts to debug logged-in page structure."""
    js = """
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
            "header.global-nav",
            "#global-nav",
            ".global-nav",
            "nav[aria-label*='Primary Navigation']",
            "input[placeholder='Search']",
            ".share-box-feed-entry__top-bar",
            "#msg-overlay",
            ".msg-overlay-container",
            ".msg-overlay-list-bubble",
            ".msg-overlay-bubble-header__badge-container"
        ];
        const out = {};
        for (const sel of selectors) {
            const seen = new WeakSet();
            let count = 0;
            for (const root of roots) {
                for (const n of deepNodes(root, sel)) {
                    if (!n || seen.has(n)) continue;
                    seen.add(n);
                    count += 1;
                }
            }
            out[sel] = count;
        }
        return out;
    """
    return driver.execute_script(js) or {}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Open signup, restore LinkedIn session, and detect floating messaging widget."
    )
    parser.add_argument(
        "--storage",
        default="/home/satyajeet/Desktop/jobs_scraper/recruiter_outreach_bot/data/linkedin_storage.json",
        help="Path to linkedin_storage.json",
    )
    parser.add_argument(
        "--signup-url",
        default="https://www.linkedin.com/signup",
        help="Page to open first before session restore",
    )
    parser.add_argument(
        "--home-url",
        default="https://www.linkedin.com/feed/",
        help="LinkedIn URL where widget should be detected",
    )
    parser.add_argument(
        "--post-login-delay",
        type=float,
        default=8.0,
        help="Extra delay after home page load before widget detection",
    )
    parser.add_argument("--headless", action="store_true", help="Run browser headless")
    parser.add_argument(
        "--wait-before-close",
        type=float,
        default=6.0,
        help="Keep browser open briefly before close",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    driver = inbox_scraper._build_driver(headless=args.headless)
    try:
        driver.get(args.signup_url)
        inbox_scraper._wait_document_ready(driver, timeout_s=40)
        time.sleep(2.0)

        cookie_count = inbox_scraper.login_linkedin_with_storage_state(
            driver,
            storage_state_path=args.storage,
            timeout_s=50,
        )

        driver.get(args.home_url)
        inbox_scraper._wait_document_ready(driver, timeout_s=40)
        time.sleep(max(0.0, args.post_login_delay))

        marker_counts = _collect_dom_marker_counts(driver)
        state = inbox_scraper.detect_floating_messaging_widget(driver)
        click_result = inbox_scraper.click_floating_messaging_widget(driver)
        time.sleep(1.2)
        conversations = inbox_scraper.extract_messaging_conversations_with_retry(
            driver,
            max_items=25,
            attempts=6,
            interval_s=1.0,
        )

        # New: Detect and click on an unread message card if exists
        unread_items = [c for c in conversations.get("items", []) if c.get("unread")]
        unread_click_result = None
        if unread_items:
            target = unread_items[0]
            target_name = target.get("profile_name", "Unknown")
            logging.info("Found %d unread messages. Attempting to click on: %s", len(unread_items), target_name)
            clicked = inbox_scraper.click_conversation_by_name(driver, target_name)
            unread_click_result = {
                "profile_name": target_name,
                "clicked": clicked
            }
            if clicked:
                logging.info("Successfully clicked on unread message card for %s", target_name)
                time.sleep(2.5) # Wait for thread to open and hydrate
                history = inbox_scraper.extract_active_thread_messages(driver)
                unread_click_result["thread_history"] = history
                logging.info("Extracted %d messages from active thread.", len(history))
            else:
                logging.warning("Failed to click on unread message card for %s", target_name)
        else:
            logging.info("No unread messages found to click.")

        screenshot_path = inbox_scraper.capture_messaging_widget_marked_screenshot(driver)

        result = {
            "ok": True,
            "signup_url_opened_first": args.signup_url,
            "home_url": args.home_url,
            "current_url": driver.current_url,
            "page_title": driver.title,
            "cookie_count_injected": cookie_count,
            "post_login_delay_s": args.post_login_delay,
            "dom_marker_counts": marker_counts,
            "floating_messaging_state": asdict(state),
            "floating_messaging_click_result": asdict(click_result),
            "unread_message_click_test": unread_click_result,
            "messaging_conversations": conversations,
            "floating_messaging_marked_screenshot": screenshot_path,
        }
        print(json.dumps(result, indent=2, ensure_ascii=False))
    finally:
        if args.wait_before_close > 0:
            time.sleep(args.wait_before_close)
        driver.quit()


if __name__ == "__main__":
    main()
