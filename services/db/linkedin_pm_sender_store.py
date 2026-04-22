import json
from typing import Any, Optional
from services.db.connection import db_session
from services.db.models import LinkedInPMSender


def upsert_linkedin_pm_sender(sender_name: str, linkedin_profile_url: Optional[str] = None) -> int:
    """Insert or update a LinkedIn PM sender by profile URL. Returns the sender ID."""
    sql = """
    INSERT INTO linkedin_pm_senders (sender_name, linkedin_profile_url)
    VALUES (%s, %s)
    ON DUPLICATE KEY UPDATE
        sender_name = VALUES(sender_name),
        updated_at = CURRENT_TIMESTAMP
    """
    with db_session() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (sender_name, linkedin_profile_url))
            # If it was an update, LAST_INSERT_ID() returns 0; fetch by URL instead.
            last_id = cur.lastrowid
            if last_id and last_id > 0:
                return int(last_id)
            # Fetch existing row
            cur.execute(
                "SELECT id FROM linkedin_pm_senders WHERE linkedin_profile_url = %s",
                (linkedin_profile_url,),
            )
            row = cur.fetchone()
            return int(row["id"]) if row else 0


def get_sender_by_url(url: str) -> Optional[LinkedInPMSender]:
    """Retrieve a LinkedIn PM sender by their profile URL."""
    sql = "SELECT * FROM linkedin_pm_senders WHERE linkedin_profile_url = %s"
    with db_session() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (url,))
            row = cur.fetchone()
            return LinkedInPMSender.model_validate(row) if row else None


def get_sender_by_id(sender_id: int) -> Optional[LinkedInPMSender]:
    """Retrieve a LinkedIn PM sender by their ID."""
    sql = "SELECT * FROM linkedin_pm_senders WHERE id = %s"
    with db_session() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (sender_id,))
            row = cur.fetchone()
            return LinkedInPMSender.model_validate(row) if row else None
