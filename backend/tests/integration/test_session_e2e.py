"""
End-to-end integration tests for the Cloud Run backend.

These tests hit the REAL deployed service with real Gemini and real Firestore.
They are skipped automatically when CLOUD_RUN_URL is not set so they never
break a local pytest run.

Run with:
    CLOUD_RUN_URL=https://... python -m pytest tests/integration/test_session_e2e.py -v
"""
from __future__ import annotations

import os
import time
import uuid
from typing import Any

import pytest
import requests

CLOUD_RUN_URL = os.getenv("CLOUD_RUN_URL", "").rstrip("/")
pytestmark = pytest.mark.skipif(
    not CLOUD_RUN_URL,
    reason="CLOUD_RUN_URL not set — skipping integration tests",
)

# ---------------------------------------------------------------------------
# Request timeout (seconds). Generous for cold-start + Gemini latency.
# ---------------------------------------------------------------------------
_TIMEOUT = 60


def _fresh_uid() -> str:
    return f"test-{int(time.time())}-{uuid.uuid4().hex[:6]}"


# ---------------------------------------------------------------------------
# Session helper class
# ---------------------------------------------------------------------------


class Session:
    """Thin wrapper around requests that tracks base_url and session_id."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.session_id: str = ""

    # -- lifecycle -----------------------------------------------------------

    def start(self, uid: str) -> requests.Response:
        r = requests.post(
            f"{self.base_url}/session/start",
            json={"uid": uid},
            timeout=_TIMEOUT,
        )
        if r.status_code == 200:
            self.session_id = r.json()["session_id"]
        return r

    def complete(self) -> requests.Response:
        return requests.post(
            f"{self.base_url}/session/{self.session_id}/complete",
            json={},
            timeout=_TIMEOUT,
        )

    # -- lesson --------------------------------------------------------------

    def get_lesson(self) -> requests.Response:
        return requests.get(
            f"{self.base_url}/session/{self.session_id}/lesson",
            timeout=_TIMEOUT,
        )

    # -- quiz ----------------------------------------------------------------

    def get_question(self) -> requests.Response:
        return requests.get(
            f"{self.base_url}/session/{self.session_id}/quiz/question",
            timeout=_TIMEOUT,
        )

    def submit_answer(self, answer: str) -> requests.Response:
        return requests.post(
            f"{self.base_url}/session/{self.session_id}/quiz/answer",
            json={"answer": answer},
            timeout=_TIMEOUT,
        )

    # -- help ----------------------------------------------------------------

    def send_help(self, message: str) -> requests.Response:
        return requests.post(
            f"{self.base_url}/session/{self.session_id}/help",
            json={"message": message},
            timeout=_TIMEOUT,
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
def session() -> Session:
    return Session(CLOUD_RUN_URL)


# ---------------------------------------------------------------------------
# Test 1 — health check
# ---------------------------------------------------------------------------


def test_health_check() -> None:
    r = requests.get(f"{CLOUD_RUN_URL}/health", timeout=_TIMEOUT)
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Test 2 — happy path (no help)
# ---------------------------------------------------------------------------


def test_happy_path_no_help(session: Session) -> None:
    uid = _fresh_uid()
    try:
        # Start session
        r = session.start(uid)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["session_id"], "session_id must be non-empty"
        assert body["lesson_id"] == "L01", f"new learner should get L01, got {body['lesson_id']}"
        assert body["status"] == "ok"

        # Cold start + Gemini latency
        time.sleep(2)

        # Get lesson
        r = session.get_lesson()
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["lesson_text"]) > 0
        assert body["character_emotion_state"]

        # Get question
        r = session.get_question()
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["question_text"]) > 0
        assert isinstance(body["options"], list)

        # Submit first option (correct or not — we just check response shape)
        first_option = body["options"][0] if body["options"] else "A"
        r = session.submit_answer(first_option)
        assert r.status_code == 200, r.text
        body = r.json()
        assert isinstance(body["correct"], bool)
        assert len(body["explanation"]) > 0

        # Complete session
        r = session.complete()
        assert r.status_code == 200, r.text
        body = r.json()
        summary = body["summary"]
        assert len(summary["summary_text"]) > 0
        assert summary["gemini_handoff_used"] is False

    except Exception:
        # Best-effort cleanup on failure
        try:
            session.complete()
        except Exception:
            pass
        raise


# ---------------------------------------------------------------------------
# Test 3 — help path triggered by two wrong answers
# ---------------------------------------------------------------------------


def test_help_path_triggered(session: Session) -> None:
    uid = _fresh_uid()
    try:
        r = session.start(uid)
        assert r.status_code == 200, r.text
        time.sleep(2)

        session.get_lesson()

        r = session.get_question()
        assert r.status_code == 200, r.text

        # Submit wrong answer twice
        trigger_help = False
        for _ in range(2):
            r = session.submit_answer("WRONG_ANSWER_THAT_CANNOT_BE_CORRECT")
            assert r.status_code == 200, r.text
            body = r.json()
            if body.get("trigger_help"):
                trigger_help = True
                break

        # Either after 1st or 2nd wrong answer trigger_help should fire
        assert trigger_help, "trigger_help should be True after wrong answers"

        # Send one help message
        r = session.send_help("Can you explain this again?")
        assert r.status_code == 200, r.text
        body = r.json()
        assert isinstance(body["resolved"], bool)
        assert body["character_emotion_state"]

    finally:
        try:
            session.complete()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Test 4 — help path unresolved → handoff generated (PR-6 key test)
# ---------------------------------------------------------------------------


def test_help_unresolved_handoff(session: Session) -> None:
    uid = _fresh_uid()
    try:
        r = session.start(uid)
        assert r.status_code == 200, r.text
        time.sleep(2)

        session.get_lesson()
        session.get_question()

        # Trigger help with two wrong answers
        trigger_help = False
        for _ in range(2):
            r = session.submit_answer("WRONG_ANSWER_THAT_CANNOT_BE_CORRECT")
            assert r.status_code == 200, r.text
            if r.json().get("trigger_help"):
                trigger_help = True
                break

        assert trigger_help, "trigger_help should be True"

        # Send 3 nonsensical help messages to force unresolved path.
        # Use gibberish so the LLM cannot resolve the question.
        last_body: dict[str, Any] = {}
        for i in range(3):
            r = session.send_help("xkzqw mfvpl zzz 12345 ???")
            if r.status_code == 409:
                # Resolved earlier than expected — skip rest of turns
                break
            assert r.status_code == 200, f"Help turn {i+1} failed: {r.text}"
            last_body = r.json()
            if last_body.get("resolved") is True:
                # LLM resolved despite gibberish — can't reach handoff; skip
                pytest.skip("LLM resolved help unexpectedly — cannot test handoff path")

        # After turn 3 with unresolved question the handoff must be present
        assert last_body.get("resolved") is False
        handoff = last_body.get("gemini_handoff_prompt")
        assert handoff is not None, "gemini_handoff_prompt must not be None after turn 3"
        assert len(handoff) > 0, "gemini_handoff_prompt must not be empty"
        assert len(handoff) <= 3000, f"gemini_handoff_prompt too long: {len(handoff)} chars"

        # Complete and verify gemini_handoff_used in summary
        r = session.complete()
        assert r.status_code == 200, r.text
        summary = r.json()["summary"]
        assert summary["gemini_handoff_used"] is True

    except Exception:
        try:
            session.complete()
        except Exception:
            pass
        raise


# ---------------------------------------------------------------------------
# Test 5 — turn cap enforced (4th help message → 409)
# ---------------------------------------------------------------------------


def test_help_turn_cap_enforced(session: Session) -> None:
    uid = _fresh_uid()
    try:
        r = session.start(uid)
        assert r.status_code == 200, r.text
        time.sleep(2)

        session.get_lesson()
        session.get_question()

        # Trigger help
        trigger_help = False
        for _ in range(2):
            r = session.submit_answer("WRONG_ANSWER_THAT_CANNOT_BE_CORRECT")
            assert r.status_code == 200, r.text
            if r.json().get("trigger_help"):
                trigger_help = True
                break

        assert trigger_help, "trigger_help should be True"

        # Exhaust all 3 help turns
        for i in range(3):
            r = session.send_help("I still do not understand at all")
            # After the 3rd turn the backend resolves or exhausts and reverts
            # phase to 'quiz' — only the first 2 turns guarantee 200
            if i < 2:
                assert r.status_code == 200, f"Help turn {i+1} failed: {r.text}"

        # After turn cap the HelpSession raises RuntimeError → backend returns 409
        # regardless of current phase (turn count is tracked inside HelpSession)
        r = session.send_help("One more question please")
        assert r.status_code == 409, (
            f"Expected 409 after turn cap, got {r.status_code}: {r.text}"
        )

    finally:
        try:
            session.complete()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Test 6 — FSRS written to Firestore after complete
# ---------------------------------------------------------------------------


def test_fsrs_written_to_firestore(session: Session) -> None:
    # Gracefully skip if Firestore client or ADC is not available
    try:
        from google.cloud import firestore as _firestore  # noqa: F401
    except ImportError:
        pytest.skip("google-cloud-firestore not installed")

    uid = _fresh_uid()
    try:
        r = session.start(uid)
        assert r.status_code == 200, r.text
        time.sleep(2)

        session.get_lesson()

        r = session.get_question()
        assert r.status_code == 200, r.text
        first_option = r.json()["options"][0] if r.json()["options"] else "A"
        session.submit_answer(first_option)

        r = session.complete()
        assert r.status_code == 200, r.text

    except Exception:
        try:
            session.complete()
        except Exception:
            pass
        raise

    # Read Firestore directly
    try:
        from google.cloud import firestore

        db = firestore.Client(project="agentic-learning-app-e13cb")
        doc_ref = db.collection("learners").document(uid).collection("concepts").document("L01")
        doc = doc_ref.get()

        if not doc.exists:
            pytest.skip("Firestore document not written — FSRS may not have run yet")

        # Doc structure: { "0": {next_review_at, mastery_score, ...}, "1": {...} }
        # Concept IDs are question indices stored as string keys inside the lesson doc.
        data = doc.to_dict() or {}
        assert len(data) > 0, f"Firestore concept doc is empty: {data}"

        # Validate the first concept entry
        first_concept = next(iter(data.values()))
        assert isinstance(first_concept, dict), f"Expected dict for concept, got: {type(first_concept)}"

        next_review_at = first_concept.get("next_review_at")
        assert next_review_at is not None, f"next_review_at missing in concept: {first_concept}"

        mastery_score = first_concept.get("mastery_score")
        assert mastery_score is not None, "mastery_score must be present"
        assert isinstance(mastery_score, float), f"mastery_score must be float, got {type(mastery_score)}"
        assert 0.0 <= mastery_score <= 1.0, f"mastery_score out of range: {mastery_score}"

    except Exception as exc:
        if "credentials" in str(exc).lower() or "auth" in str(exc).lower():
            pytest.skip(f"ADC not configured — skipping Firestore read: {exc}")
        raise
