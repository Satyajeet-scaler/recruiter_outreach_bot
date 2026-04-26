import json
import logging
import os
from time import sleep
from typing import Any, Callable, TypeVar

import requests

logger = logging.getLogger(__name__)

T = TypeVar("T")

def _get_slack_defaults():
    return {
        "webhook_url": os.getenv("SLACK_WEBHOOK_URL"),
        "channel": os.getenv("SLACK_CHANNEL", "recruiter-outreach-alerts"),
        "username": os.getenv("SLACK_USERNAME", "Outreach Bot"),
        "icon_emoji": os.getenv("SLACK_ICON_EMOJI", ":bell:"),
    }

def retry_slack_action(
    action: Callable[[], T],
    *,
    retries: int = 3,
    initial_delay_seconds: float = 1.0,
) -> T:
    delay = initial_delay_seconds
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            return action()
        except Exception as exc:
            last_error = exc
            if attempt == retries - 1:
                break
            sleep(delay)
            delay *= 2
    raise RuntimeError(f"Slack post failed after {retries} attempts: {last_error}") from last_error

def send_slack_text(text: str) -> bool:
    """POST one message using env defaults. Returns False if webhook missing."""
    defaults = _get_slack_defaults()
    url = defaults["webhook_url"]
    if not url:
        logger.info("slack notification skipped: SLACK_WEBHOOK_URL not configured")
        return False

    payload = {
        "text": text,
        "channel": defaults["channel"],
        "username": defaults["username"],
        "icon_emoji": defaults["icon_emoji"],
    }

    try:
        retry_slack_action(
            lambda: requests.post(
                url,
                data={"payload": json.dumps(payload, ensure_ascii=True)},
                timeout=20,
            ).raise_for_status()
        )
        return True
    except Exception as exc:
        logger.error(f"Failed to send slack message: {exc}")
        return False

def notify_intent_event(conversation_id: int, intent: str, recruiter_name: str, profile_url: str):
    """Format and send a Slack notification for a specific intent event."""
    text = (
        f"🔔 *Intent Alert: {intent}*\n"
        f"Recruiter: {recruiter_name}\n"
        f"Profile: {profile_url}\n"
        f"Conversation: {conversation_id}\n"
    )
    return send_slack_text(text)
