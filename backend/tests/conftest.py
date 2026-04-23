"""
Shared pytest fixtures for session API tests.

Patches all external I/O used by main.py endpoints so tests run without
Gemini API keys, Firestore credentials, or any real network calls.

Strategy: patch at the main.py boundary (the import sites), not deep inside
sub-modules. This keeps tests fast, deterministic, and insulated from SDK
implementation details.

Patches applied per-function (autouse):
- main._read_learner_concepts  → returns [] (new learner → L01/beginner)
- main.LessonSession           → MagicMock class; each instance has predictable
                                  teach/next_question/evaluate_answer responses
- main._summary_call.run_summary (via summary_call module patch) → returns a
                                  fixed summary dict
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixed response payloads used by the mock LessonSession
# ---------------------------------------------------------------------------

_TEACH_RESULT: dict[str, Any] = {
    "lesson_text": "Welcome to the lesson!",
    "character_emotion_state": "teaching",
    "key_concepts": ["concept_a", "concept_b"],
}

_QUESTION_RESULT: dict[str, Any] = {
    "question_text": "What does ls do?",
    "format": "multiple_choice",
    "options": ["Lists files", "Moves files", "Deletes files", "Copies files"],
    "character_emotion_state": "curious",
}

_ANSWER_RESULT: dict[str, Any] = {
    "correct": True,
    "explanation": "Correct! ls lists directory contents.",
    "concept_score_delta": 0.1,
    "character_emotion_state": "celebrating",
    "trigger_help": False,
}

_HELP_RESULT: dict[str, Any] = {
    "resolved": False,
    "character_emotion_state": "helping",
    "gemini_handoff_prompt": None,
}

_SUMMARY_RESULT: dict[str, Any] = {
    "session_id": "test-session",
    "uid": "test-uid",
    "lesson_id": "L01",
    "tier": "beginner",
    "quiz_scores": {},
    "time_on_task_seconds": 120,
    "help_triggered": False,
    "gemini_handoff_used": False,
    "summary_text": "Great session!",
    "concept_outcomes": {},
    "fsrs_results": {},
    "completed_at": "2026-04-15T00:00:00+00:00",
}


def _make_lesson_session_instance() -> MagicMock:
    """Build a fresh MagicMock instance behaving like a LessonSession."""
    instance = MagicMock()
    instance.teach.return_value = _TEACH_RESULT
    instance.next_question.return_value = _QUESTION_RESULT
    instance.evaluate_answer.return_value = _ANSWER_RESULT
    # help_session is None until trigger_help fires (tests can override)
    instance.help_session = None
    return instance


# ---------------------------------------------------------------------------
# Autouse per-function fixture: patch external I/O for every test
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _patch_external_io(monkeypatch: pytest.MonkeyPatch) -> Any:
    """
    Patch all external I/O at the main.py boundary for every test.

    Uses monkeypatch for clean teardown.  The LessonSession class is replaced
    with a factory that always returns a fresh mock instance, so each call to
    session_start gets an independent mock with its own call counts.
    """
    # Each instantiation of LessonSession returns a new fresh mock
    def _lesson_session_factory(*args: Any, **kwargs: Any) -> MagicMock:
        return _make_lesson_session_instance()

    mock_lesson_session_cls = MagicMock(side_effect=_lesson_session_factory)

    with (
        patch("main._read_learner_concepts", return_value=[]),
        patch("main.LessonSession", mock_lesson_session_cls),
        patch("main.check_rate_limit"),  # bypass rate limiter in unit tests
        patch("summary_call.genai.Client") as mock_summary_genai,
        patch("summary_call.firestore.Client"),
    ):
        # summary_call.run_summary needs genai to return a parseable response
        import json
        mock_resp = MagicMock()
        mock_resp.text = json.dumps({
            "summary_text": "Great session!",
            "concept_outcomes": {},
        })
        mock_summary_genai.return_value.models.generate_content.return_value = mock_resp
        yield
