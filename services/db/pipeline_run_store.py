import json
from typing import Any, Optional
from services.db.connection import db_session

def start_pipeline_run(run_type: str) -> int:
    """Initialize a new pipeline execution record."""
    sql = "INSERT INTO pipeline_runs (run_type, status, started_at) VALUES (%s, 'running', CURRENT_TIMESTAMP)"
    with db_session() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (run_type,))
            return int(cur.lastrowid)

def finish_pipeline_run(run_id: int, status: str = "completed", stats: Optional[dict[str, Any]] = None):
    """Mark a pipeline execution as finished and record statistics."""
    sql = """
    UPDATE pipeline_runs 
    SET status = %(status)s, 
        finished_at = CURRENT_TIMESTAMP,
        conversations_scanned = %(scanned)s,
        messages_processed = %(processed)s,
        replies_sent = %(sent)s,
        errors_json = CAST(%(errors)s AS JSON)
    WHERE id = %(id)s
    """
    s = stats or {}
    payload = {
        "id": run_id,
        "status": status,
        "scanned": s.get("conversations_scanned", 0),
        "processed": s.get("messages_processed", 0),
        "sent": s.get("replies_sent", 0),
        "errors": json.dumps(s.get("errors", []), default=str) if s.get("errors") else None
    }
    with db_session() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, payload)
