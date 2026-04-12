-- 001_create_schema.sql
-- Initial schema for the Linux Basics learning app.
--
-- Prerequisites:
--   - PostgreSQL 17 instance with the learning_app database
--   - pgvector extension enabled (run enable_pgvector.sh first)
--
-- Run via:
--   ./infra/scripts/apply_schema.sh
--
-- This script is idempotent: safe to re-run on an existing database.

-- Guard in case apply_schema.sh is run before enable_pgvector.sh.
CREATE EXTENSION IF NOT EXISTS vector;

-- ---------------------------------------------------------------------------
-- ENUM types
-- ---------------------------------------------------------------------------

-- difficulty_tier: lowercase to match generated content filenames and embed output.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'difficulty_tier') THEN
        CREATE TYPE difficulty_tier AS ENUM ('beginner', 'intermediate', 'advanced');
    END IF;
END
$$;

-- question_format: short codes used in Cloud SQL; mapped from generation JSON in seed_db.py.
--   multiple_choice -> mc
--   true_false      -> tf
--   fill_blank      -> fill
--   command_completion -> command
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'question_format') THEN
        CREATE TYPE question_format AS ENUM ('mc', 'tf', 'fill', 'command');
    END IF;
END
$$;

-- ---------------------------------------------------------------------------
-- lessons
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS lessons (
    lesson_id     TEXT    PRIMARY KEY,          -- e.g. 'L04'
    module_id     INTEGER NOT NULL,             -- 1–9
    title         TEXT    NOT NULL,
    prerequisites TEXT[]  NOT NULL DEFAULT '{}',
    concept_tags  TEXT[]  NOT NULL DEFAULT '{}'  -- seeded empty; filled in Phase 1.1
);

-- ---------------------------------------------------------------------------
-- content_chunks
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS content_chunks (
    chunk_id     UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    lesson_id    TEXT            NOT NULL REFERENCES lessons(lesson_id),
    tier         difficulty_tier NOT NULL,
    content_text TEXT            NOT NULL,
    embedding    vector(768),
    token_count  INTEGER         NOT NULL DEFAULT 0,
    UNIQUE (lesson_id, tier)     -- one chunk per lesson × tier in MVP
);

-- ivfflat index for cosine similarity search.
-- lists=10 is appropriate for ~87 rows (target: sqrt(row_count) ≈ 9).
CREATE INDEX IF NOT EXISTS content_chunks_embedding_idx
    ON content_chunks
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 10);

-- ---------------------------------------------------------------------------
-- quiz_questions
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS quiz_questions (
    question_id            TEXT            PRIMARY KEY,  -- e.g. 'L04-B-Q01'
    lesson_id              TEXT            NOT NULL REFERENCES lessons(lesson_id),
    tier                   difficulty_tier NOT NULL,
    format                 question_format NOT NULL,
    question_text          TEXT            NOT NULL,
    correct_answer         TEXT            NOT NULL,
    distractors            TEXT[]          NOT NULL DEFAULT '{}',
    explanation            TEXT            NOT NULL,
    options_json           JSONB,                        -- full options[] for multiple_choice
    learning_objective_ref TEXT
);

CREATE INDEX IF NOT EXISTS quiz_questions_lesson_tier_idx
    ON quiz_questions (lesson_id, tier);
