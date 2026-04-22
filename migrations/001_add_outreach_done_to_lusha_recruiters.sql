-- Migration: Add outreach tracking columns to lusha_recruiters
-- Run this once against your MySQL database before using the lusha outreach cron.

ALTER TABLE lusha_recruiters
  ADD COLUMN outreach_done TINYINT(1) NOT NULL DEFAULT 0,
  ADD COLUMN outreach_done_at DATETIME DEFAULT NULL;

-- Optional index for the cron query
CREATE INDEX idx_lusha_recruiters_outreach_done ON lusha_recruiters (outreach_done);
