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

def classify_intent(messages: list[dict[str, Any]]) -> IntentLabel:
    """Classify the recruiter's intent based on the conversation history."""
    if not messages:
        return IntentLabel.NEUTRAL

    # Format conversation history for the prompt
    history_lines = []
    for m in messages:
        sender = "Recruiter" if m.get("sender_type") == "recruiter" else "Assistant"
        # content_text is the field in models.py
        history_lines.append(f"{sender}: {m.get('content_text', '')}")
    history_text = "\n".join(history_lines)

    model = _get_gemini_model()
    prompt = f"""You are an AI assistant for a recruiter outreach bot. 
Analyze the following conversation history between our Assistant and a Recruiter. 
Classify the RECRUITER'S LATEST intent into exactly one of these labels:

- positive: They expressed clear interest in reviewing candidates or posting a job on our platform without needing further convincing.
- positive_clarification: They expressed interest or openness (e.g., "looks good", "sounds interesting"), but THEN asked a question or for clarification.
- clarifying_doubts: They are asking questions or expressing doubts WITHOUT yet showing interest. Use this if they keep asking questions and haven't said it looks good or they want to proceed.
- want_top_candidates: They explicitly asked to see our top candidates or requested profiles/resumes.
- neutral: They acknowledged the message but didn't commit, or said something generic that isn't clearly positive or negative.
- non_relevant: They expressed no interest, told us to stop, or the message is an automated reply.

Conversation History:
{history_text}

Return ONLY the label: positive, positive_clarification, clarifying_doubts, want_top_candidates, neutral, or non_relevant. Do not explain or add preamble."""
    
    try:
        response = model.generate_content(prompt)
        raw = response.text.strip().lower()
        if "positive_clarification" in raw:
            return IntentLabel.POSITIVE_CLARIFICATION
        if "clarifying_doubts" in raw:
            return IntentLabel.CLARIFYING_DOUBTS
        if "want_top_candidates" in raw:
            return IntentLabel.WANT_TOP_CANDIDATES
        if "positive" in raw: 
            return IntentLabel.POSITIVE
        if "non_relevant" in raw or "not_relevant" in raw: 
            return IntentLabel.NON_RELEVANT
        return IntentLabel.NEUTRAL
    except Exception as exc:
        logger.error(f"Gemini intent classification failed: {exc}")
        return IntentLabel.NEUTRAL

def process_latest_message_intent(conversation_id: int, latest_message_text: str):
    """Analyze the latest inbound message in context of the full conversation and update state."""
    from services.db.message_store import get_messages_by_conversation
    
    # Fetch all messages to provide full context to the classifier
    messages = get_messages_by_conversation(conversation_id)
    # Convert models to dicts for the classifier
    message_dicts = [m.model_dump() for m in messages]
    
    intent = classify_intent(message_dicts)
    
    convo = get_conversation_by_id(conversation_id)
    if not convo:
        logger.error(f"Conversation {conversation_id} not found for intent processing.")
        return
        
    context = convo.conversation_context_json or {}
    context["last_intent"] = intent.value
    context["last_analyzed_at"] = os.popen('date -Iseconds').read().strip()
    
    update_conversation_intent_and_context(conversation_id, intent, context)
    logger.info(f"Conversation {conversation_id}: intent classified as {intent.value}")
    return intent
