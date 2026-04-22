"""LinkedIn inbox monitoring package."""

from .inbox_scraper import (
    FloatingMessagingClickResult,
    MessagingConversationSnapshot,
    FloatingMessagingState,
    InboxScraperConfig,
    click_floating_messaging_widget,
    extract_messaging_conversations,
    extract_messaging_conversations_with_retry,
    bootstrap_inbox_scraper,
)

__all__ = [
    "FloatingMessagingClickResult",
    "MessagingConversationSnapshot",
    "FloatingMessagingState",
    "InboxScraperConfig",
    "click_floating_messaging_widget",
    "extract_messaging_conversations",
    "extract_messaging_conversations_with_retry",
    "bootstrap_inbox_scraper",
]
