-- Migration: Add job_relevance linking to lusha_recruiters
-- This applies the job relevance changes assuming outreach_done was already added.

ALTER TABLE lusha_recruiters
  ADD COLUMN job_relevance_id BIGINT UNSIGNED NULL;

-- Foreign key linking back to the relevant job intent
ALTER TABLE lusha_recruiters
  ADD CONSTRAINT fk_lusha_recruiters_job_relevance 
  FOREIGN KEY (job_relevance_id) REFERENCES job_relevance(id) 
  ON DELETE SET NULL;
