from datetime import datetime
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, field_validator
import json

class IntentLabel(str, Enum):
    POSITIVE = "positive"
    POSITIVE_CLARIFICATION = "positive_clarification"
    NEUTRAL = "neutral"
    NON_RELEVANT = "non_relevant"
    CLARIFYING_DOUBTS = "clarifying_doubts"
    WANT_TOP_CANDIDATES = "want_top_candidates"

class DeliveryStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    DELIVERED = "delivered"
    FAILED = "failed"
    BLOCKED = "blocked"

class OwnerType(str, Enum):
    RECRUITER_CONVERSATION = "recruiter_conversation"
    LINKEDIN_SENDER = "linkedin_sender"

class LinkedInPMSender(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Optional[int] = None
    sender_name: str
    linkedin_profile_url: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

class RecruiterConversation(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    
    id: Optional[int] = None
    recruiter_id: Optional[int] = None
    channel: str = "linkedin"
    thread_external_id: Optional[str] = None
    campaign_name: Optional[str] = None
    status: str = "active"
    current_intent: Optional[IntentLabel] = None
    auto_reply_enabled: bool = True
    conversation_context_json: Optional[dict[str, Any]] = None
    last_message_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @field_validator('conversation_context_json', mode='before')
    @classmethod
    def parse_json_dict(cls, v: Any) -> Any:
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                pass
        return v

class ConversationMessage(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    
    id: Optional[int] = None
    conversation_id: Optional[int] = None
    owner_type: OwnerType = OwnerType.RECRUITER_CONVERSATION
    owner_id: Optional[int] = None
    message_external_id: Optional[str] = None
    sender_type: str  # 'recruiter', 'bot', 'human_agent', 'system'
    direction: str    # 'inbound', 'outbound', 'internal'
    message_type: str = "text"
    content_text: Optional[str] = None
    message_context_json: Optional[dict[str, Any]] = None
    context_source: Optional[str] = None
    parent_message_id: Optional[int] = None
    sent_at: Optional[datetime] = None
    received_at: Optional[datetime] = None
    delivery_status: Optional[DeliveryStatus] = None
    pipeline_run_id: Optional[int] = None
    created_at: Optional[datetime] = None

    @field_validator('message_context_json', mode='before')
    @classmethod
    def parse_json_dict(cls, v: Any) -> Any:
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                pass
        return v
