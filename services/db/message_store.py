import json
from typing import Any, List, Optional
from services.db.connection import db_session
from services.db.models import ConversationMessage

def _json_dump(v: Any) -> Optional[str]:
    return json.dumps(v, default=str) if v is not None else None

def save_message(message: ConversationMessage) -> int:
    """Insert a new message into conversation_messages table."""
    sql = """
    INSERT INTO conversation_messages (
        conversation_id, owner_type, owner_id, message_external_id, sender_type, direction,
        message_type, content_text, message_context_json, context_source,
        parent_message_id, sent_at, received_at, delivery_status, pipeline_run_id
    ) VALUES (
        %(conversation_id)s, %(owner_type)s, %(owner_id)s, %(message_external_id)s, %(sender_type)s, %(direction)s,
        %(message_type)s, %(content_text)s, CAST(%(message_context_json)s AS JSON), %(context_source)s,
        %(parent_message_id)s, %(sent_at)s, %(received_at)s, %(delivery_status)s, %(pipeline_run_id)s
    )
    """
    payload = message.model_dump()
    payload["message_context_json"] = _json_dump(payload["message_context_json"])
    
    with db_session() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, payload)
            new_id = cur.lastrowid
            return int(new_id)

def get_messages_by_conversation(conversation_id: int) -> List[ConversationMessage]:
    """Retrieve all messages for a specific conversation ordered by creation time."""
    sql = "SELECT * FROM conversation_messages WHERE conversation_id = %s ORDER BY created_at ASC"
    with db_session() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (conversation_id,))
            rows = cur.fetchall()
            return [ConversationMessage.model_validate(row) for row in rows]

def update_message_delivery_status(message_id: int, status: str):
    """Update delivery status of an existing message."""
    sql = "UPDATE conversation_messages SET delivery_status = %s WHERE id = %s"
    with db_session() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (status, message_id))

def get_pending_outbound_messages() -> List[ConversationMessage]:
    """Retrieve messages marked as 'pending' delivery status."""
    sql = "SELECT * FROM conversation_messages WHERE delivery_status = 'pending' AND direction = 'outbound'"
    with db_session() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
            return [ConversationMessage.model_validate(row) for row in rows]

def get_approved_outbound_messages() -> List[ConversationMessage]:
    """Retrieve messages marked as 'sent' (policy approved) but not yet 'delivered'."""
    sql = "SELECT * FROM conversation_messages WHERE delivery_status = 'sent' AND direction = 'outbound'"
    with db_session() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
            return [ConversationMessage.model_validate(row) for row in rows]
