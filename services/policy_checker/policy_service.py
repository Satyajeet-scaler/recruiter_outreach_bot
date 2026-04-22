import logging
import re
from typing import List, Optional, Tuple
from services.db.models import DeliveryStatus
from services.db.message_store import update_message_delivery_status
from services.db.connection import db_session

logger = logging.getLogger(__name__)

class PolicyRule:
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
    
    def check(self, text: str) -> Tuple[bool, Optional[str]]:
        """Return (Passed, Reason if failed)."""
        raise NotImplementedError

class MaxLengthRule(PolicyRule):
    def __init__(self, limit: int = 500):
        super().__init__("max_length", f"Message must be under {limit} characters.")
        self.limit = limit
        
    def check(self, text: str) -> Tuple[bool, Optional[str]]:
        if len(text) > self.limit:
            return False, f"Length {len(text)} exceeds limit {self.limit}"
        return True, None

class NoPhoneNumbersRule(PolicyRule):
    def __init__(self):
        super().__init__("no_phone_numbers", "Prevent sharing phone numbers to avoid platform leakage or policy issues.")
        self.pattern = re.compile(r"(\+?\d{1,4}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}")
        
    def check(self, text: str) -> Tuple[bool, Optional[str]]:
        if self.pattern.search(text):
            return False, "Detected potential phone number"
        return True, None

class NoEmailLeakingRule(PolicyRule):
    def __init__(self):
        super().__init__("no_email_leaking", "Prevent sharing emails unless specifically allowed.")
        self.pattern = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
        
    def check(self, text: str) -> Tuple[bool, Optional[str]]:
        if self.pattern.search(text):
            return False, "Detected potential email address"
        return True, None

DEFAULT_RULES = [
    MaxLengthRule(500),
    NoPhoneNumbersRule(),
    NoEmailLeakingRule()
]

def log_policy_result(message_id: int, rule_name: str, action: str, reason: Optional[str], content: str, run_id: Optional[int] = None):
    """Save policy check result to the database."""
    sql = """
    INSERT INTO policy_logs (message_id, pipeline_run_id, rule_name, action, reason, draft_content)
    VALUES (%s, %s, %s, %s, %s, %s)
    """
    with db_session() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (message_id, run_id, rule_name, action, reason, content))

def check_message_policy(message_id: int, message_text: str, run_id: Optional[int] = None) -> bool:
    """Run all safety rules and update message delivery status."""
    all_passed = True
    for rule in DEFAULT_RULES:
        passed, reason = rule.check(message_text)
        action = "approve" if passed else "block"
        log_policy_result(message_id, rule.name, action, reason, message_text, run_id)
        if not passed:
            all_passed = False
            logger.warning(f"Message {message_id} blocked by rule {rule.name}: {reason}")

    new_status = DeliveryStatus.SENT if all_passed else DeliveryStatus.BLOCKED
    update_message_delivery_status(message_id, new_status.value)
    return all_passed
