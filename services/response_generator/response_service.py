import os
import logging
from typing import Optional
import google.generativeai as genai
from services.db.models import ConversationMessage, DeliveryStatus
from services.db.message_store import save_message, get_messages_by_conversation
from services.db.conversation_store import get_conversation_by_id

logger = logging.getLogger(__name__)

def _get_gemini_model():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    genai.configure(api_key=api_key)
    model_name = os.environ.get("GEMINI_RESPONSE_MODEL", "gemini-2.5-flash")
    return genai.GenerativeModel(model_name)

def draft_response(conversation_id: int) -> Optional[int]:
    """
    Generate a draft reply using Gemini based on conversation history and metadata.
    Saves the result as a 'pending' message in the database.
    """
    convo = get_conversation_by_id(conversation_id)
    if not convo:
        logger.error(f"Conversation {conversation_id} not found.")
        return None
        
    if not convo.auto_reply_enabled:
        logger.info(f"Auto-reply disabled for conversation {conversation_id}. Skipping.")
        return None

    # Pull history
    messages = get_messages_by_conversation(conversation_id)
    history_lines = []
    for m in messages:
        role = "Recruiter" if m.sender_type == "recruiter" else "Scaler (Us)"
        history_lines.append(f"{role}: {m.content_text}")
    
    history_text = "\n".join(history_lines)
    
    # Cumulative context
    context = convo.conversation_context_json or {}
    
    model = _get_gemini_model()
    prompt = f"""You are an AI assistant for Scaler, an upskilling platform. 
Conversation History:
{history_text}

Additional Context:
{context}+

Guidelines:
1. NEVER ask to jump on a "call", ask for a "phone number", ask for an "email", or ask to move the conversation off LinkedIn. This triggers spam filters. Everything must stay on LinkedIn.
2. If they ask "tell me more", provide a short 2-sentence explanation of how our candidates are heavily screened and ready to interview. Do not immediately try to hard-sell a job post in the same breath. Treat it like a natural chat.
3. If they are ready to see candidates, offer to send them the LinkedIn profiles of our top matched candidates right here in the chat.
4. If they explicitly want to use our platform, offer to send them a direct link to post the job.
5. Be helpful, professional, and conversational. Sound like a human peer, not an automated bot.
6. Absolute maximum 500 characters, ideally much shorter (2-3 sentences).
7. No preamble or explanation. Just the message text.

Response:"""

    try:
        response = model.generate_content(prompt)
        text = response.text.strip()
        
        # Create pending message
        new_msg = ConversationMessage(
            conversation_id=conversation_id,
            sender_type="bot",
            direction="outbound",
            message_type="text",
            content_text=text,
            delivery_status=DeliveryStatus.PENDING,
            context_source="response_generator.response_service"
        )
        msg_id = save_message(new_msg)
        logger.info(f"Drafted response for convo {conversation_id}: msg_id={msg_id}")
        return msg_id
        
    except Exception as exc:
        logger.error(f"Gemini response generation failed: {exc}")
        return None
