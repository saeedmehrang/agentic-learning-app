"""
Edge-case tests for the session API HTTP layer.

Covers input validation, concurrent session isolation, and boundary
conditions not exercised by the main test_session_api.py suite.
"""
from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from main import _sessions, app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture(autouse=True)
def _clear():
    _sessions.clear()
    yield
    _sessions.clear()


UID = "edge-case-user"


def _start(client: TestClient, uid: str = UID) -> str:
    resp = client.post("/session/start", json={"uid": uid})
    assert resp.status_code == 200
    return resp.json()["session_id"]


def _to_quiz(client: TestClient, sid: str) -> None:
    resp = client.get(f"/session/{sid}/lesson")
    assert resp.status_code == 200


def _set_phase(sid: str, phase: str) -> None:
    from unittest.mock import MagicMock
    _sessions[sid].phase = phase
    # When forcing to help phase, inject a mock help_session so the endpoint
    # doesn't 409 on "no active HelpSession".
    if phase == "help" and _sessions[sid].lesson_session.help_session is None:
        mock_help = MagicMock()
        mock_help.respond.return_value = {
            "resolved": False,
            "character_emotion_state": "helping",
            "gemini_handoff_prompt": None,
        }
        _sessions[sid].lesson_session.help_session = mock_help


# ---------------------------------------------------------------------------
# POST /session/start — input validation
# ---------------------------------------------------------------------------


class TestSessionStartValidation:
    def test_empty_uid_string_is_accepted(self, client: TestClient) -> None:
        """FastAPI accepts empty string — UID validation is app-level concern."""
        resp = client.post("/session/start", json={"uid": ""})
        assert resp.status_code == 200

    def test_uid_with_special_characters_accepted(self, client: TestClient) -> None:
        resp = client.post("/session/start", json={"uid": "user@example.com/test:123"})
        assert resp.status_code == 200

    def test_extra_fields_are_ignored(self, client: TestClient) -> None:
        resp = client.post("/session/start", json={"uid": UID, "unexpected": "field"})
        assert resp.status_code == 200

    def test_wrong_content_type_returns_422(self, client: TestClient) -> None:
        resp = client.post(
            "/session/start",
            content="uid=test",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Concurrent sessions (same UID, multiple sessions)
# ---------------------------------------------------------------------------


class TestConcurrentSessions:
    def test_two_concurrent_sessions_for_same_uid_are_independent(
        self, client: TestClient
    ) -> None:
        sid1 = _start(client, uid="shared-uid")
        sid2 = _start(client, uid="shared-uid")
        assert sid1 != sid2
        assert sid1 in _sessions
        assert sid2 in _sessions

    def test_completing_one_session_does_not_affect_the_other(
        self, client: TestClient
    ) -> None:
        sid1 = _start(client, uid="shared-uid")
        sid2 = _start(client, uid="shared-uid")
        client.post(f"/session/{sid1}/complete", json={})
        assert sid1 not in _sessions
        assert sid2 in _sessions

    def test_lesson_phase_isolated_between_sessions(self, client: TestClient) -> None:
        sid1 = _start(client)
        sid2 = _start(client, uid="other-user")
        # Advance sid1 to quiz
        _to_quiz(client, sid1)
        # sid2 should still be in lesson phase
        assert _sessions[sid2].phase == "lesson"


# ---------------------------------------------------------------------------
# POST /session/{id}/complete — time_on_task edge cases
# ---------------------------------------------------------------------------


class TestCompleteTimeOnTask:
    def test_negative_time_on_task_is_passed_through(
        self, client: TestClient
    ) -> None:
        """Negative values are non-zero so passed through as-is (no server-side clamp).
        The client is responsible for sending a valid value."""
        sid = _start(client)
        resp = client.post(f"/session/{sid}/complete", json={"time_on_task_seconds": -1})
        assert resp.status_code == 200
        assert resp.json()["summary"]["time_on_task_seconds"] == -1

    def test_very_large_time_on_task_passed_through(self, client: TestClient) -> None:
        """No upper bound enforced — large values passed through as-is."""
        sid = _start(client)
        resp = client.post(
            f"/session/{sid}/complete", json={"time_on_task_seconds": 86400}
        )
        assert resp.json()["summary"]["time_on_task_seconds"] == 86400

    def test_missing_body_uses_default_zero(self, client: TestClient) -> None:
        """time_on_task_seconds defaults to 0 → falls back to elapsed."""
        sid = _start(client)
        resp = client.post(f"/session/{sid}/complete", json={})
        assert resp.status_code == 200
        assert resp.json()["summary"]["time_on_task_seconds"] >= 0


# ---------------------------------------------------------------------------
# Phase state machine — invalid transitions
# ---------------------------------------------------------------------------


class TestInvalidPhaseTransitions:
    def test_lesson_endpoint_rejected_after_complete(self, client: TestClient) -> None:
        """Once completed, the session is gone — lesson returns 404."""
        sid = _start(client)
        client.post(f"/session/{sid}/complete", json={})
        resp = client.get(f"/session/{sid}/lesson")
        assert resp.status_code == 404

    def test_quiz_question_rejected_in_lesson_phase(self, client: TestClient) -> None:
        sid = _start(client)
        resp = client.get(f"/session/{sid}/quiz/question")
        assert resp.status_code == 409

    def test_quiz_answer_rejected_in_lesson_phase(self, client: TestClient) -> None:
        sid = _start(client)
        resp = client.post(f"/session/{sid}/quiz/answer", json={"answer": "A"})
        assert resp.status_code == 409

    def test_help_rejected_in_quiz_phase(self, client: TestClient) -> None:
        sid = _start(client)
        _to_quiz(client, sid)
        resp = client.post(f"/session/{sid}/help", json={"message": "help"})
        assert resp.status_code == 409

    def test_lesson_rejected_in_quiz_phase(self, client: TestClient) -> None:
        sid = _start(client)
        _to_quiz(client, sid)
        resp = client.get(f"/session/{sid}/lesson")
        assert resp.status_code == 409

    def test_409_detail_names_current_phase(self, client: TestClient) -> None:
        """The 409 error detail must identify the current phase for debuggability."""
        sid = _start(client)
        resp = client.get(f"/session/{sid}/quiz/question")
        assert resp.status_code == 409
        assert "lesson" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Session ID format
# ---------------------------------------------------------------------------


class TestSessionIdFormat:
    def test_session_id_is_valid_uuid(self, client: TestClient) -> None:
        import uuid
        resp = client.post("/session/start", json={"uid": UID})
        sid = resp.json()["session_id"]
        parsed = uuid.UUID(sid)  # raises if invalid UUID
        assert str(parsed) == sid

    def test_session_id_version_4(self, client: TestClient) -> None:
        import uuid
        resp = client.post("/session/start", json={"uid": UID})
        sid = resp.json()["session_id"]
        assert uuid.UUID(sid).version == 4


# ---------------------------------------------------------------------------
# Exception-handler paths in main.py endpoints (500 responses)
# ---------------------------------------------------------------------------


class TestEndpointExceptionHandlers:
    """Verify that unexpected exceptions in endpoint logic produce HTTP 500."""

    def test_session_start_500_on_unexpected_error(self, client: TestClient) -> None:
        from unittest.mock import patch
        with patch("main._read_learner_concepts", side_effect=RuntimeError("db exploded")):
            resp = client.post("/session/start", json={"uid": UID})
        assert resp.status_code == 500

    def test_get_lesson_500_on_teach_exception(self, client: TestClient) -> None:
        sid = _start(client)
        _sessions[sid].lesson_session.teach.side_effect = RuntimeError("gemini down")
        resp = client.get(f"/session/{sid}/lesson")
        assert resp.status_code == 500

    def test_get_quiz_question_500_on_unexpected_exception(self, client: TestClient) -> None:
        sid = _start(client)
        _to_quiz(client, sid)
        _sessions[sid].lesson_session.next_question.side_effect = RuntimeError("unexpected")
        resp = client.get(f"/session/{sid}/quiz/question")
        assert resp.status_code == 500

    def test_get_quiz_question_409_when_exhausted(self, client: TestClient) -> None:
        sid = _start(client)
        _to_quiz(client, sid)
        _sessions[sid].lesson_session.next_question.side_effect = IndexError("no more questions")
        resp = client.get(f"/session/{sid}/quiz/question")
        assert resp.status_code == 409
        assert "No more" in resp.json()["detail"]

    def test_submit_quiz_answer_500_on_unexpected_exception(self, client: TestClient) -> None:
        sid = _start(client)
        _to_quiz(client, sid)
        _sessions[sid].lesson_session.evaluate_answer.side_effect = RuntimeError("fail")
        resp = client.post(f"/session/{sid}/quiz/answer", json={"answer": "A"})
        assert resp.status_code == 500

    def test_submit_quiz_answer_sets_help_phase_on_trigger(self, client: TestClient) -> None:
        """trigger_help=True in evaluate_answer result must flip phase to 'help'."""
        from unittest.mock import MagicMock
        sid = _start(client)
        _to_quiz(client, sid)
        _sessions[sid].lesson_session.evaluate_answer.return_value = {
            "correct": False,
            "explanation": "Wrong!",
            "concept_score_delta": -0.1,
            "character_emotion_state": "encouraging",
            "trigger_help": True,
        }
        mock_help = MagicMock()
        mock_help.respond.return_value = {
            "resolved": False,
            "character_emotion_state": "helping",
            "gemini_handoff_prompt": None,
        }
        _sessions[sid].lesson_session.help_session = mock_help
        resp = client.post(f"/session/{sid}/quiz/answer", json={"answer": "wrong"})
        assert resp.status_code == 200
        assert resp.json()["trigger_help"] is True
        assert _sessions[sid].phase == "help"

    def test_help_turn_409_when_help_session_is_none(self, client: TestClient) -> None:
        """help_session=None while phase='help' must return 409."""
        sid = _start(client)
        _sessions[sid].phase = "help"
        _sessions[sid].lesson_session.help_session = None
        resp = client.post(f"/session/{sid}/help", json={"message": "help"})
        assert resp.status_code == 409

    def test_help_turn_500_on_unexpected_exception(self, client: TestClient) -> None:
        sid = _start(client)
        _set_phase(sid, "help")
        _sessions[sid].lesson_session.help_session.respond.side_effect = ValueError("bad")
        resp = client.post(f"/session/{sid}/help", json={"message": "help"})
        assert resp.status_code == 500

    def test_complete_session_500_on_run_summary_exception(self, client: TestClient) -> None:
        from unittest.mock import patch
        sid = _start(client)
        with patch("summary_call.run_summary", side_effect=RuntimeError("summary failed")):
            resp = client.post(f"/session/{sid}/complete", json={"time_on_task_seconds": 60})
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# _read_learner_concepts — Firestore failure path (lines 224-247)
# ---------------------------------------------------------------------------


class TestReadLearnerConcepts:
    def test_firestore_failure_returns_empty_list(self, client: TestClient) -> None:
        """Firestore error during concepts read must fall back to [] (new learner)."""
        from unittest.mock import patch
        with patch("google.cloud.firestore.Client", side_effect=Exception("Firestore unavailable")):
            resp = client.post("/session/start", json={"uid": "brand-new-user"})
        # Should succeed — scheduler falls back to L01/beginner for empty concepts
        assert resp.status_code == 200

    def test_missing_lesson_content_falls_back_to_empty(self, client: TestClient) -> None:
        """session_start warns and uses empty lesson content when key not in _lesson_store."""
        from unittest.mock import patch
        import main as main_mod
        original_store = dict(main_mod._lesson_store)
        try:
            main_mod._lesson_store.clear()
            resp = client.post("/session/start", json={"uid": UID})
        finally:
            main_mod._lesson_store.update(original_store)
        assert resp.status_code == 200

    def test_firestore_success_path_returns_concepts(self, client: TestClient) -> None:
        """_read_learner_concepts happy path: Firestore stream returns concept documents."""
        from unittest.mock import MagicMock, patch

        mock_doc = MagicMock()
        mock_doc.id = "L01"
        mock_doc.to_dict.return_value = {"mastery_score": 0.6, "fsrs_stability": 2.0}

        mock_collection = MagicMock()
        mock_collection.stream.return_value = [mock_doc]

        mock_db = MagicMock()
        mock_db.collection.return_value.document.return_value.collection.return_value = (
            mock_collection
        )

        with patch("google.cloud.firestore.Client", return_value=mock_db):
            resp = client.post("/session/start", json={"uid": "returning-user"})

        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# except HTTPException: raise paths — endpoints re-raise 409s from within try blocks
# ---------------------------------------------------------------------------


class TestHttpExceptionReraise:
    """The except HTTPException: raise guard inside each endpoint's try block."""

    def test_lesson_endpoint_propagates_http_exception_from_teach(
        self, client: TestClient
    ) -> None:
        """If teach() itself raises HTTPException, it must be re-raised (not wrapped as 500)."""
        from unittest.mock import patch
        from fastapi import HTTPException as FastApiHTTPException
        sid = _start(client)
        _sessions[sid].lesson_session.teach.side_effect = FastApiHTTPException(
            status_code=409, detail="inner 409"
        )
        resp = client.get(f"/session/{sid}/lesson")
        assert resp.status_code == 409

    def test_quiz_question_endpoint_propagates_http_exception(
        self, client: TestClient
    ) -> None:
        from unittest.mock import patch
        from fastapi import HTTPException as FastApiHTTPException
        sid = _start(client)
        _to_quiz(client, sid)
        _sessions[sid].lesson_session.next_question.side_effect = FastApiHTTPException(
            status_code=409, detail="inner 409"
        )
        resp = client.get(f"/session/{sid}/quiz/question")
        assert resp.status_code == 409

    def test_quiz_answer_endpoint_propagates_http_exception(
        self, client: TestClient
    ) -> None:
        from fastapi import HTTPException as FastApiHTTPException
        sid = _start(client)
        _to_quiz(client, sid)
        _sessions[sid].lesson_session.evaluate_answer.side_effect = FastApiHTTPException(
            status_code=409, detail="inner 409"
        )
        resp = client.post(f"/session/{sid}/quiz/answer", json={"answer": "A"})
        assert resp.status_code == 409

    def test_help_endpoint_propagates_http_exception(
        self, client: TestClient
    ) -> None:
        from fastapi import HTTPException as FastApiHTTPException
        sid = _start(client)
        _set_phase(sid, "help")
        _sessions[sid].lesson_session.help_session.respond.side_effect = (
            FastApiHTTPException(status_code=409, detail="inner 409")
        )
        resp = client.post(f"/session/{sid}/help", json={"message": "help"})
        assert resp.status_code == 409

    def test_help_runtime_error_becomes_409(self, client: TestClient) -> None:
        """Line 521: RuntimeError from help session (e.g. 4th turn cap) → 409."""
        sid = _start(client)
        _set_phase(sid, "help")
        _sessions[sid].lesson_session.help_session.respond.side_effect = RuntimeError(
            "Exceeded 3 turns"
        )
        resp = client.post(f"/session/{sid}/help", json={"message": "help"})
        assert resp.status_code == 409
        assert "Exceeded 3 turns" in resp.json()["detail"]

    def test_complete_endpoint_propagates_http_exception(
        self, client: TestClient
    ) -> None:
        """Line 581: HTTPException raised inside complete must be re-raised, not wrapped as 500."""
        from unittest.mock import patch
        from fastapi import HTTPException as FastApiHTTPException
        sid = _start(client)
        with patch(
            "summary_call.run_summary",
            side_effect=FastApiHTTPException(status_code=409, detail="inner complete 409"),
        ):
            resp = client.post(f"/session/{sid}/complete", json={})
        assert resp.status_code == 409
