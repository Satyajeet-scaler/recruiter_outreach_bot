import os
import json
import logging
from typing import Any, Optional
import google.generativeai as genai
from services.db.models import IntentLabel
from services.db.conversation_store import update_conversation_intent_and_context, get_conversation_by_id

logger = logging.getLogger(__name__)

def _get_gemini_model():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    genai.configure(api_key=api_key)
    # Using flash for efficiency/latency in classification
    model_name = os.environ.get("GEMINI_CLASSIFIER_MODEL", "gemini-2.5-flash")
    return genai.GenerativeModel(model_name)

def classify_intent(message_text: str) -> IntentLabel:
    """Classify the recruiter's message into positive, positive_clarification, neutral, or non_relevant."""
    if not message_text.strip():
        return IntentLabel.NEUTRAL

    model = _get_gemini_model()
    prompt = f"""You are an AI assistant for a recruiter outreach bot. 
Analyze the following message from a recruiter and classify their intent into exactly one of these labels:
- positive: They expressed clear interest in reviewing candidates or posting a job on our platform without needing further convincing.
- positive_clarification: They expressed interest or openness, but explicitly asked for more details, raised a concern, or requested clarification before they can proceed.
- neutral: They acknowledged the message but didn't commit, or said something generic that isn't clearly positive or negative.
- non_relevant: They expressed no interest, told us to stop, or the message is a pure automated out-of-office reply.

Message: "{message_text}"

Return ONLY the label: positive, positive_clarification, neutral, or non_relevant. Do not explain or add preamble."""
    
    try:
        response = model.generate_content(prompt)
        raw = response.text.strip().lower()
        if "positive_clarification" in raw:
            return IntentLabel.POSITIVE_CLARIFICATION
        if "positive" in raw: 
            return IntentLabel.POSITIVE
        if "non_relevant" in raw or "not_relevant" in raw: 
            return IntentLabel.NON_RELEVANT
        return IntentLabel.NEUTRAL
    except Exception as exc:
        logger.error(f"Gemini intent classification failed: {exc}")
        return IntentLabel.NEUTRAL

def process_latest_message_intent(conversation_id: int, latest_message_text: str):
    """Analyze the latest inbound message and update the conversation thread-level state."""
    intent = classify_intent(latest_message_text)
    
    convo = get_conversation_by_id(conversation_id)
    if not convo:
        logger.error(f"Conversation {conversation_id} not found for intent processing.")
        return
        
    context = convo.conversation_context_json or {}
    context["last_intent"] = intent.value
    context["last_analyzed_at"] = os.popen('date -Iseconds').read().strip()
    
    # We could also append to messages history here if needed for cumulative context
    
    update_conversation_intent_and_context(conversation_id, intent, context)
    logger.info(f"Conversation {conversation_id}: intent classified as {intent.value}")
    return intent
