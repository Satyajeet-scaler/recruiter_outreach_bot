ALTER TABLE recruiter_conversations MODIFY COLUMN current_intent ENUM('neutral', 'positive', 'negative', 'not_applicable', 'positive_clarification') DEFAULT 'not_applicable';
