import logging
import os
import time
from typing import Any, Dict, Optional
from services.db.message_store import update_message_delivery_status
from services.db.conversation_store import update_conversation_intent_and_context, get_conversation_by_id
from services.linkedin_recruiter.message_sender import _send_message_with_driver
from services.db.models import DeliveryStatus

logger = logging.getLogger(__name__)

def send_to_recruiter(driver, profile_url: str, message_text: str, message_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Send a message to a recruiter via their profile URL.
    Directly uses the existing message_sender service.
    If message_id is provided, updates its status in the DB upon success.
    """
    logger.info(f"SenderService: Sending to {profile_url}")
    
    # 1. Execute Sending via LinkedIn Message Sender
    result = _send_message_with_driver(driver, profile_url, message_text)
    success = result.get("message_sent", False)
    
    if success:
        logger.info(f"SenderService: Successfully sent message to {profile_url}")
        
        # 2. Update DB status if we have a message record
        if message_id:
            update_message_delivery_status(message_id, DeliveryStatus.DELIVERED.value)
            
            # 3. Update Conversation Context
            # We try to find the conversation via the message if possible, 
            # but usually the caller handles the orchestration.
            # For simplicity, we assume the caller will handle the thread-level context update
            # or we can try to resolve it if needed.
    else:
        reason = result.get("reason", "unknown")
        logger.error(f"SenderService: Failed to send to {profile_url}. Reason: {reason}")
        
    return result

def process_approved_messages(driver):
    """
    Orchestrator that finds approved messages and sends them using send_to_recruiter.
    """
    from services.db.message_store import get_approved_outbound_messages
    from services.db.recruiter_store import get_recruiter_url_by_id

    approved = get_approved_outbound_messages()
    for msg in approved:
        convo = get_conversation_by_id(msg.conversation_id)
        if not convo: continue
        
        url = get_recruiter_url_by_id(convo.recruiter_id)
        if not url: continue
        
        res = send_to_recruiter(driver, url, msg.content_text, message_id=msg.id)
        if res.get("message_sent"):
            # Update history context
            context = convo.conversation_context_json
            if not isinstance(context, dict):
                context = {}
            
            history = context.get("history", [])
            history.append({
                "role": "bot",
                "content": msg.content_text,
                "timestamp": os.popen('date -u +"%Y-%m-%dT%H:%M:%SZ"').read().strip()
            })
            context["history"] = history
            update_conversation_intent_and_context(msg.conversation_id, convo.current_intent, context)
