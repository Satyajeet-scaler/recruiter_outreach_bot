import os
import sys
import logging

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s'
)

from services.linkedin_inbox.inbox_scraper import bootstrap_inbox_scraper, InboxScraperConfig
cfg = InboxScraperConfig(
    watcher_mode=True, 
    watch_interval_s=60,
    headless=False # Set to False to watch the browser actions
)
bootstrap_inbox_scraper(cfg)