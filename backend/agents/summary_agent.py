"""
SummaryAgent — evaluates a completed lesson session and updates spaced-repetition
schedules via the run_fsrs tool.

Responsibilities:
- Call run_fsrs for each concept touched during the session
- Write a session record to Firestore: learners/UID/sessions/SESSION_ID
- Write per-concept mastery documents to Firestore: learners/UID/concepts/CONCEPT_ID
- Output a structured JSON summary of the session
- NEVER write gemini_handoff_prompt content to Firestore — gemini_handoff_used is
  a boolean flag only (privacy constraint)
"""
from __future__ import annotations

import logging

from google.adk.agents import LlmAgent

from config import settings
from tools.run_fsrs import run_fsrs

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Session record schema — used by tests to verify field correctness
# ---------------------------------------------------------------------------

SESSION_RECORD_SCHEMA: dict[str, type] = {
    "lesson_id": str,
    "tier_used": str,
    "quiz_scores": dict,
    "time_on_task_seconds": int,
    "help_triggered": bool,
    "gemini_handoff_used": bool,
    "summary_text": str,
    "created_at": str,
}

# ---------------------------------------------------------------------------
# System instruction
# ---------------------------------------------------------------------------

SUMMARY_AGENT_INSTRUCTION = """
You are the SummaryAgent for an adaptive Linux learning platform.

Your job is to evaluate the completed lesson session and persist all outcomes to
Firestore, then return a structured summary.

## Step 1 — Run FSRS for each concept
For every concept that was touched in the session, call the `run_fsrs` tool once
with the concept's current fsrs_stability, fsrs_difficulty, mastery_score, and the
outcome ("correct" or "incorrect") based on the quiz result for that concept.

Use the quiz_scores dict to determine outcomes: a score >= 0.7 is "correct",
below 0.7 is "incorrect".

## Step 2 — Write session record
Write a Firestore document at path: learners/UID/sessions/SESSION_ID
(where UID is the learner uid and SESSION_ID is a new UUID)

Required fields:
- lesson_id (string): ID of the lesson completed
- tier_used (string): difficulty tier used — "beginner", "intermediate", or "advanced"
- quiz_scores (dict): map of concept_id → score (0.0–1.0)
- time_on_task_seconds (int): total seconds the learner spent in the session
- help_triggered (bool): true if the learner invoked HelpAgent at least once
- gemini_handoff_used (bool): true if HelpAgent escalated to the Gemini app.
  IMPORTANT PRIVACY CONSTRAINT: gemini_handoff_used MUST be stored as a boolean
  flag only. NEVER write the gemini_handoff_prompt text content to Firestore.
  Do not log, store, or echo the prompt content anywhere.
- summary_text (string): 2–3 sentence human-readable session summary
- created_at (string): ISO 8601 UTC timestamp of session completion

## Step 3 — Write concept mastery records
For each concept reviewed, write a Firestore document at path: learners/UID/concepts/CONCEPT_ID
(where UID is the learner uid and CONCEPT_ID is the concept identifier)

Required fields:
- mastery_score (float): updated mastery from run_fsrs output
- fsrs_stability (float): updated stability from run_fsrs output
- fsrs_difficulty (float): updated difficulty from run_fsrs output
- last_review_at (string): ISO 8601 UTC timestamp of this review
- next_review_at (string): ISO 8601 UTC timestamp from run_fsrs output
- review_count (int): increment the existing count by 1

## Step 4 — Output
Respond with ONLY a valid JSON object matching this schema — no other text:
{
  "lesson_id": "<lesson ID>",
  "tier_used": "<beginner|intermediate|advanced>",
  "quiz_scores": {"<concept_id>": <score>, ...},
  "time_on_task_seconds": <int>,
  "help_triggered": <true|false>,
  "gemini_handoff_used": <true|false>,
  "summary_text": "<2–3 sentence summary>",
  "created_at": "<ISO 8601 UTC timestamp>"
}

## Privacy reminder
gemini_handoff_used is boolean only. You have exactly one allowed value for this
field: true or false. Never include any prompt text, conversation excerpt, or
handoff content in any Firestore write or in your JSON output.
"""

# ---------------------------------------------------------------------------
# Agent definition
# ---------------------------------------------------------------------------

summary_agent = LlmAgent(
    name="summary_agent",
    model=settings.summary_agent_model,
    instruction=SUMMARY_AGENT_INSTRUCTION,
    tools=[run_fsrs],
    output_key="summary_output",
)
