import json
from typing import Any, Optional
from services.db.connection import db_session
from services.db.models import RecruiterConversation, IntentLabel

def _json_dump(v: Any) -> Optional[str]:
    return json.dumps(v, default=str) if v is not None else None

def _normalize_conversation_row(row: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Normalize DB JSON-ish fields so Pydantic receives Python dicts."""
    if not row:
        return row
    normalized = dict(row)
    raw_ctx = normalized.get("conversation_context_json")
    if isinstance(raw_ctx, str):
        try:
            parsed = json.loads(raw_ctx)
            normalized["conversation_context_json"] = parsed if isinstance(parsed, dict) else {}
        except Exception:
            normalized["conversation_context_json"] = {}
    return normalized

def get_conversation_by_id(conversation_id: int) -> Optional[RecruiterConversation]:
    """Retrieve a conversation by its primary key."""
    sql = "SELECT * FROM recruiter_conversations WHERE id = %s"
    with db_session() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (conversation_id,))
            row = cur.fetchone()
            return RecruiterConversation.model_validate(_normalize_conversation_row(row)) if row else None

def get_conversation_by_external_id(thread_external_id: str) -> Optional[RecruiterConversation]:
    """Retrieve a conversation by its external LinkedIn thread ID."""
    sql = "SELECT * FROM recruiter_conversations WHERE thread_external_id = %s"
    with db_session() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (thread_external_id,))
            row = cur.fetchone()
            return RecruiterConversation.model_validate(_normalize_conversation_row(row)) if row else None

def get_conversation_by_recruiter_id(recruiter_id: int, channel: str = "linkedin") -> Optional[RecruiterConversation]:
    """Retrieve a conversation for a specific recruiter on a given channel."""
    sql = "SELECT * FROM recruiter_conversations WHERE recruiter_id = %s AND channel = %s"
    with db_session() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (recruiter_id, channel))
            row = cur.fetchone()
            return RecruiterConversation.model_validate(_normalize_conversation_row(row)) if row else None

def upsert_conversation(conversation: RecruiterConversation) -> int:
    """Insert or update a recruiter conversation record."""
    payload = conversation.model_dump()
    payload["conversation_context_json"] = _json_dump(payload["conversation_context_json"])

    if conversation.id is not None:
        sql = """
        UPDATE recruiter_conversations SET
            recruiter_id = %(recruiter_id)s,
            channel = %(channel)s,
            thread_external_id = %(thread_external_id)s,
            campaign_name = %(campaign_name)s,
            status = %(status)s,
            current_intent = %(current_intent)s,
            auto_reply_enabled = %(auto_reply_enabled)s,
            conversation_context_json = CAST(%(conversation_context_json)s AS JSON),
            last_message_at = %(last_message_at)s,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = %(id)s
        """
        with db_session() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, payload)
        return conversation.id
    else:
        sql = """
        INSERT INTO recruiter_conversations (
            recruiter_id, channel, thread_external_id, campaign_name, status,
            current_intent, auto_reply_enabled, conversation_context_json, last_message_at
        ) VALUES (
            %(recruiter_id)s, %(channel)s, %(thread_external_id)s, %(campaign_name)s, %(status)s,
            %(current_intent)s, %(auto_reply_enabled)s, CAST(%(conversation_context_json)s AS JSON), %(last_message_at)s
        )
        """
        with db_session() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, payload)
                cur.execute("SELECT LAST_INSERT_ID() as id")
                res = cur.fetchone()
                return int(res["id"])

def update_conversation_intent_and_context(conversation_id: int, intent: IntentLabel, context: dict[str, Any]):
    """Update intent and cumulative context for a conversation."""
    sql = """
    UPDATE recruiter_conversations 
    SET current_intent = %s, 
        conversation_context_json = CAST(%s AS JSON),
        updated_at = CURRENT_TIMESTAMP
    WHERE id = %s
    """
    with db_session() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (intent, _json_dump(context), conversation_id))
