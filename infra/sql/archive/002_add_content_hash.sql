-- 002_add_content_hash.sql
-- Add content_hash column to lessons, content_chunks, and quiz_questions.
--
-- This enables hash-based change detection in seed_db.py: rows are only
-- written when the incoming content_hash differs from the stored one,
-- making re-seed runs a no-op when content is unchanged.
--
-- Apply via:
--   gcloud sql connect learning-app-db --user=postgres --database=learning_app \
--     < infra/sql/002_add_content_hash.sql

ALTER TABLE lessons
    ADD COLUMN IF NOT EXISTS content_hash TEXT;

ALTER TABLE content_chunks
    ADD COLUMN IF NOT EXISTS content_hash TEXT;

ALTER TABLE quiz_questions
    ADD COLUMN IF NOT EXISTS content_hash TEXT;
