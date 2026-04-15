"""
summary_call.py — Single-shot Gemini summary call + FSRS update + Firestore writes.

Called at the end of each learning session (POST /session/{id}/complete).

Flow
----
1. Build a single generate_content() prompt from session data.
2. Parse the summary text and per-concept scores from the Gemini response.
3. Run run_fsrs() for each concept touched in the session.
4. Write the session record to Firestore: learners/{uid}/sessions/{session_id}
5. Update each concept doc:           learners/{uid}/concepts/{lesson_id}
6. Return the session record dict to the caller (main.py).

Design notes
------------
- No chat history — a single generate_content() call, not client.chats.create().
- gemini_handoff_used is stored as a boolean, never the prompt string (privacy rule).
- run_fsrs() is pure Python — no model call inside FSRS logic.
- Firestore writes use the google-cloud-firestore async client via SERVER_TIMESTAMP.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import google.genai as genai
from google.cloud import firestore
from google.genai import types as genai_types

from config import settings
from tools.run_fsrs import run_fsrs

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default FSRS parameters for a concept that has never been reviewed.
# ---------------------------------------------------------------------------
_DEFAULT_STABILITY: float = 1.0
_DEFAULT_DIFFICULTY: float = 5.0
_DEFAULT_MASTERY: float = 0.0

# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def _build_summary_prompt(session_data: dict[str, Any]) -> str:
    """Build the prompt for the single-shot Gemini summary call."""
    lesson_id = session_data.get("lesson_id", "unknown")
    tier = session_data.get("tier", "beginner")
    quiz_scores = session_data.get("quiz_scores", {})
    time_on_task = session_data.get("time_on_task_seconds", 0)
    help_triggered = session_data.get("help_triggered", False)
    gemini_handoff_used = session_data.get("gemini_handoff_used", False)

    return f"""You are a learning coach summarising a completed lesson session.

SESSION DATA
------------
Lesson ID          : {lesson_id}
Difficulty tier    : {tier}
Quiz scores        : {json.dumps(quiz_scores, ensure_ascii=False)}
Time on task (s)   : {time_on_task}
Help triggered     : {help_triggered}
Gemini handoff used: {gemini_handoff_used}

TASK
----
Write a brief, encouraging personalised summary (2–4 sentences) for the learner.
Highlight what they did well and what to focus on in their next session.

Return ONLY valid JSON matching this schema exactly:
{{
  "summary_text": "<2-4 sentence summary for the learner>",
  "concept_outcomes": {{
    "<concept_id>": "correct" | "incorrect"
  }}
}}

The concept_outcomes map must contain one entry per concept that appeared in the quiz.
Use the quiz_scores keys as concept IDs (they are already present in the SESSION DATA).
Set the outcome to "correct" if the quiz score for that concept is > 0, otherwise "incorrect".
"""


# ---------------------------------------------------------------------------
# JSON helpers (re-used from lesson_session pattern)
# ---------------------------------------------------------------------------


def _require_text(response: Any, context: str) -> str:
    text = response.text
    if text is None:
        raise ValueError(
            f"Gemini returned no text (possible safety block) in {context}"
        )
    return text


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"No JSON object found in summary response: {text[:200]!r}")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"Failed to parse JSON from summary response: {exc}") from exc


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_summary(session_data: dict[str, Any]) -> dict[str, Any]:
    """
    Run the summary call for a completed session, update FSRS, write Firestore.

    Args:
        session_data: Dict containing:
            uid (str): Firebase UID of the learner.
            session_id (str): Session identifier.
            lesson_id (str): e.g. "L01".
            tier (str): "beginner" | "intermediate" | "advanced".
            quiz_scores (dict[str, float]): concept_id → score delta accumulated
                during the quiz phase (positive = net correct, zero/negative = wrong).
            time_on_task_seconds (int): Wall-clock seconds spent in the session.
            help_triggered (bool): Whether HelpSession was invoked.
            gemini_handoff_used (bool): Whether the Gemini deep-link was shown.
            concept_fsrs (dict[str, dict]): Optional. Current FSRS state per concept
                from Firestore. Keys are concept IDs; each value has:
                    fsrs_stability (float), fsrs_difficulty (float), mastery_score (float).
                Missing concepts use _DEFAULT_STABILITY / _DEFAULT_DIFFICULTY / _DEFAULT_MASTERY.

    Returns:
        Session record dict written to Firestore, plus:
            summary_text (str): Personalised summary for the learner.
            fsrs_results (dict[str, dict]): Per-concept FSRS update results.
    """
    uid: str = session_data["uid"]
    session_id: str = session_data.get("session_id") or str(uuid.uuid4())
    lesson_id: str = session_data["lesson_id"]
    tier: str = session_data.get("tier", "beginner")
    quiz_scores: dict[str, float] = session_data.get("quiz_scores", {})
    time_on_task: int = int(session_data.get("time_on_task_seconds", 0))
    help_triggered: bool = bool(session_data.get("help_triggered", False))
    gemini_handoff_used: bool = bool(session_data.get("gemini_handoff_used", False))
    concept_fsrs: dict[str, dict[str, Any]] = session_data.get("concept_fsrs", {})

    # ------------------------------------------------------------------
    # 1. Single-shot Gemini summary call
    # ------------------------------------------------------------------
    client = genai.Client()
    prompt = _build_summary_prompt(session_data)

    try:
        response = client.models.generate_content(
            model=settings.summary_model,
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                temperature=0.4,
            ),
        )
        raw = _extract_json(_require_text(response, "run_summary"))
    except Exception as exc:
        logger.error("Summary Gemini call failed for %s/%s: %s", uid, lesson_id, exc,
                     exc_info=True)
        raise

    summary_text: str = raw.get("summary_text", "")
    concept_outcomes: dict[str, str] = raw.get("concept_outcomes", {})

    # If Gemini returned no concept outcomes, fall back to deriving from quiz_scores
    if not concept_outcomes:
        concept_outcomes = {
            cid: ("correct" if score > 0 else "incorrect")
            for cid, score in quiz_scores.items()
        }

    # ------------------------------------------------------------------
    # 2. Run FSRS for each concept touched
    # ------------------------------------------------------------------
    fsrs_results: dict[str, dict[str, Any]] = {}
    for concept_id, outcome in concept_outcomes.items():
        prior = concept_fsrs.get(concept_id, {})
        stability = float(prior.get("fsrs_stability", _DEFAULT_STABILITY))
        difficulty = float(prior.get("fsrs_difficulty", _DEFAULT_DIFFICULTY))
        mastery = float(prior.get("mastery_score", _DEFAULT_MASTERY))

        # Ensure stability is positive (guard against corrupt Firestore data)
        if stability <= 0.0:
            stability = _DEFAULT_STABILITY

        valid_outcome = outcome if outcome in ("correct", "incorrect") else "incorrect"
        try:
            fsrs_results[concept_id] = run_fsrs(
                concept_id, stability, difficulty, mastery, valid_outcome
            )
        except ValueError as exc:
            logger.warning(
                "run_fsrs failed for concept %s (outcome=%r): %s — skipping",
                concept_id, valid_outcome, exc,
            )

    # ------------------------------------------------------------------
    # 3. Build the session record
    # ------------------------------------------------------------------
    completed_at = datetime.now(UTC).isoformat()
    session_record: dict[str, Any] = {
        "session_id": session_id,
        "uid": uid,
        "lesson_id": lesson_id,
        "tier": tier,
        "quiz_scores": quiz_scores,
        "time_on_task_seconds": time_on_task,
        "help_triggered": help_triggered,
        "gemini_handoff_used": gemini_handoff_used,
        "summary_text": summary_text,
        "concept_outcomes": concept_outcomes,
        "fsrs_results": fsrs_results,
        "completed_at": completed_at,
    }

    # ------------------------------------------------------------------
    # 4. Firestore writes
    # ------------------------------------------------------------------
    _write_to_firestore(uid, session_id, lesson_id, session_record, fsrs_results)

    return session_record


def _write_to_firestore(
    uid: str,
    session_id: str,
    lesson_id: str,
    session_record: dict[str, Any],
    fsrs_results: dict[str, dict[str, Any]],
) -> None:
    """
    Write session record and concept FSRS updates to Firestore.

    Paths:
        learners/{uid}/sessions/{session_id}  — full session record
        learners/{uid}/concepts/{lesson_id}   — FSRS state (merged, not overwritten)

    Both writes are best-effort: errors are logged but not re-raised so that
    a Firestore outage does not block the HTTP response to the Flutter client.
    """
    try:
        db = firestore.Client(project=settings.gcp_project_id)

        # Write session record
        session_ref = (
            db.collection("learners")
            .document(uid)
            .collection("sessions")
            .document(session_id)
        )
        session_ref.set(session_record)
        logger.info(
            "Firestore session record written",
            extra={"uid": uid, "session_id": session_id, "lesson_id": lesson_id},
        )

        # Update each concept's FSRS state under learners/{uid}/concepts/{lesson_id}
        # We store all concepts for the same lesson under one document keyed by lesson_id.
        if fsrs_results:
            concept_ref = (
                db.collection("learners")
                .document(uid)
                .collection("concepts")
                .document(lesson_id)
            )
            concept_update: dict[str, Any] = {}
            for concept_id, result in fsrs_results.items():
                concept_update[concept_id] = {
                    "fsrs_stability": result["fsrs_stability"],
                    "fsrs_difficulty": result["fsrs_difficulty"],
                    "mastery_score": result["mastery_score"],
                    "next_review_at": result["next_review_at"],
                    "last_reviewed_at": session_record["completed_at"],
                }
            concept_ref.set(concept_update, merge=True)
            logger.info(
                "Firestore concept FSRS updated",
                extra={
                    "uid": uid,
                    "lesson_id": lesson_id,
                    "concept_count": len(fsrs_results),
                },
            )
    except Exception as exc:
        logger.error(
            "Firestore write failed for uid=%s session=%s: %s",
            uid, session_id, exc, exc_info=True,
        )
