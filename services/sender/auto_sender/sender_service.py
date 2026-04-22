import logging
import time
import os
from services.db.message_store import get_approved_outbound_messages, update_message_delivery_status
from services.db.conversation_store import get_conversation_by_id, update_conversation_intent_and_context
from services.db.recruiter_store import get_recruiter_url_by_id
from services.linkedin_recruiter.message_sender import _send_message_with_driver
from services.db.models import DeliveryStatus

logger = logging.getLogger(__name__)

def deliver_approved_messages(driver):
    """
    Orchestrate the delivery of all policy-approved messages.
    Navigates to each recruiter's profile, sends the message, and updates the DB.
    """
    approved_messages = get_approved_outbound_messages()
    if not approved_messages:
        logger.info("AutoSender: No approved messages found in queue.")
        return 0

    success_count = 0
    for msg in approved_messages:
        try:
            # 1. Resolve Recruiter URL
            # The recruiter_id is linked via the conversation thread
            convo = get_conversation_by_id(msg.conversation_id)
            if not convo:
                logger.error(f"AutoSender: Conversation {msg.conversation_id} not found for message {msg.id}")
                continue
                
            recruiter_url = get_recruiter_url_by_id(convo.recruiter_id)
            if not recruiter_url:
                logger.error(f"AutoSender: Recruiter URL not found for recruiter {convo.recruiter_id}")
                continue

            logger.info(f"AutoSender: Sending msg {msg.id} to {recruiter_url}")
            
            # 2. Execute Sending
            # _send_message_with_driver handles navigation, clicking 'Message', and typing
            success, reason, _ = _send_message_with_driver(driver, recruiter_url, msg.content_text)
            
            if success:
                # 3. Update Message Status
                update_message_delivery_status(msg.id, DeliveryStatus.DELIVERED.value)
                success_count += 1
                logger.info(f"AutoSender: Message {msg.id} delivered.")
                
                # 4. Update Cumulative Context
                # Fetch fresh convo state for the update
                context = convo.conversation_context_json or {}
                history = context.get("history", [])
                history.append({
                    "role": "bot",
                    "content": msg.content_text,
                    "timestamp": os.popen('date -u +"%Y-%m-%dT%H:%M:%SZ"').read().strip()
                })
                context["history"] = history
                # Persist the updated history and current intent
                update_conversation_intent_and_context(msg.conversation_id, convo.current_intent, context)
            else:
                logger.error(f"AutoSender: Failed to send {msg.id} to {recruiter_url}. Reason: {reason}")
                # Optional: escalate or mark as failed
                
        except Exception as exc:
            logger.exception(f"AutoSender: Critical failure while processing msg {msg.id}: {exc}")
            
    return success_count
