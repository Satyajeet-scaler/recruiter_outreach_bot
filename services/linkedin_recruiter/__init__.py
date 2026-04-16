"""LinkedIn recruiter utilities for profile automation."""

from services.linkedin_recruiter.ellipsis_menu_service import (
    click_profile_ellipsis_and_get_menu_options,
    click_profile_ellipsis_and_get_menu_options_sync,
)
from services.linkedin_recruiter.connection_request_sender import (
    send_connection_request_sync,
)
from services.linkedin_recruiter.profile_connection_degree_service import (
    find_connection_degree_by_profile_url,
    find_connection_degree_by_profile_url_sync,
)
from services.linkedin_recruiter.outreach_orchestrator import (
    run_outreach_batch_sync,
)

# Optional: connections module requires bs4/lxml.
try:
    from services.linkedin_recruiter.connections import (
        get_linkedin_profile_connection_degree,
        get_linkedin_profile_connection_degree_sync,
        is_linkedin_profile_url,
        parse_profile_connection_degree,
        scrape_linkedin_profile_connection_degrees,
        scrape_linkedin_profile_connection_degrees_sync,
    )
except ModuleNotFoundError:
    pass

__all__ = [
    "click_profile_ellipsis_and_get_menu_options",
    "click_profile_ellipsis_and_get_menu_options_sync",
    "send_connection_request_sync",
    "find_connection_degree_by_profile_url",
    "find_connection_degree_by_profile_url_sync",
    "run_outreach_batch_sync",
    "get_linkedin_profile_connection_degree",
    "get_linkedin_profile_connection_degree_sync",
    "is_linkedin_profile_url",
    "parse_profile_connection_degree",
    "scrape_linkedin_profile_connection_degrees",
    "scrape_linkedin_profile_connection_degrees_sync",
]
