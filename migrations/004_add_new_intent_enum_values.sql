ALTER TABLE recruiter_conversations 
  MODIFY COLUMN current_intent 
  ENUM('neutral','positive','negative','not_applicable','positive_clarification','non_relevant','clarifying_doubts','want_top_candidates') 
  DEFAULT 'not_applicable';
