"""
SummaryAgent — evaluates a completed lesson session and updates spaced-repetition
schedules via the run_fsrs tool.

Responsibilities:
- Call run_fsrs for each concept touched during the session
- Call run_fsrs for each concept touched during the session
- Output a structured JSON summary (Firestore writes are handled by main.py)
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

You receive a single message with session facts. Your job is:

1. Call the `run_fsrs` tool once for the concept studied, using these defaults if
   not provided: fsrs_stability=1.0, fsrs_difficulty=5.0, mastery_score=0.5.
   Determine outcome: quiz score >= 0.7 is "correct", below 0.7 is "incorrect".

2. Respond with ONLY a valid JSON object — no prose, no markdown fences:

{
  "lesson_id": "<lesson ID from input>",
  "tier_used": "<tier from input>",
  "quiz_scores": {"<concept_id>": <score>},
  "time_on_task_seconds": <int from input>,
  "help_triggered": <true|false>,
  "gemini_handoff_used": <true|false>,
  "summary_text": "<2-3 sentence human-readable summary of the session>",
  "created_at": "<current UTC timestamp in ISO 8601 format>",
  "fsrs_result": <the full object returned by run_fsrs>
}

Privacy: gemini_handoff_used is boolean only — never include prompt text.
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
