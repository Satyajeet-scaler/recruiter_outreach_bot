"""LinkedIn inbox monitoring package."""

from .inbox_scraper import (
    FloatingMessagingClickResult,
    MessagingConversationSnapshot,
    FloatingMessagingState,
    InboxScraperConfig,
    click_floating_messaging_widget,
    extract_messaging_conversations,
    extract_messaging_conversations_with_retry,
    run_inbox_scraper_bootstrap,
)

__all__ = [
    "FloatingMessagingClickResult",
    "MessagingConversationSnapshot",
    "FloatingMessagingState",
    "InboxScraperConfig",
    "click_floating_messaging_widget",
    "extract_messaging_conversations",
    "extract_messaging_conversations_with_retry",
    "run_inbox_scraper_bootstrap",
]
