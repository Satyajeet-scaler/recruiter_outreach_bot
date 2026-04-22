from typing import Optional
from services.db.connection import db_session
from urllib.parse import urlparse, urlunparse

def _normalized_linkedin_profile_url(value: str) -> Optional[str]:
    raw = str(value or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        return raw.rstrip("/")
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", "", ""))

def get_recruiter_id_by_linkedin_url(linkedin_url: str) -> Optional[int]:
    """Look up a recruiter's internal PK by their LinkedIn profile URL."""
    norm_url = _normalized_linkedin_profile_url(linkedin_url)
    if not norm_url:
        return None
        
    sql = "SELECT id FROM lusha_recruiters WHERE linkedin_url = %s OR linkedin_url = %s"
    with db_session() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (norm_url, norm_url + "/"))
            row = cur.fetchone()
            return int(row["id"]) if row else None

def get_recruiter_url_by_id(recruiter_id: int) -> Optional[str]:
    """Look up a recruiter's LinkedIn URL by their internal PK."""
    sql = "SELECT linkedin_url FROM lusha_recruiters WHERE id = %s"
    with db_session() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (recruiter_id,))
            row = cur.fetchone()
            return str(row["linkedin_url"]) if row else None


def get_pending_outreach_recruiters() -> list[dict]:
    """Return all lusha_recruiters where outreach has not been done yet.

    Joins with job_relevance, jobs, and job_scrapes to get full JD context.
    Each dict contains: id, linkedin_url, name, job_url, job_title, company, job_description.
    """
    sql = """
        SELECT 
            lr.id,
            lr.linkedin_url,
            lr.full_name AS name,
            j.job_url_normalized AS job_url,
            COALESCE(j.title, lr.job_title) as job_title,
            COALESCE(j.company, lr.company_name) as company,
            js.description_full AS job_description
        FROM lusha_recruiters lr
        LEFT JOIN job_relevance jr ON lr.job_relevance_id = jr.id
        LEFT JOIN jobs j ON jr.job_id = j.id
        LEFT JOIN job_scrapes js ON j.id = js.job_id
        WHERE lr.outreach_done = 0
    """
    with db_session() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
            return [dict(r) for r in rows] if rows else []


def mark_outreach_done(recruiter_id: int) -> None:
    """Mark a recruiter as outreach-complete."""
    sql = """
    UPDATE lusha_recruiters
    SET outreach_done = 1,
        outreach_done_at = CURRENT_TIMESTAMP
    WHERE id = %s
    """
    with db_session() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (recruiter_id,))
