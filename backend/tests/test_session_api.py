"""
Integration tests for the FastAPI session lifecycle (all 6 endpoints).

Uses httpx AsyncClient driven by the ASGI app directly — no network.
The OTel tracer is initialised at import time in main.py; tests run
against the same in-process app state. Each test that creates a session
cleans up after itself to avoid cross-test pollution.
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
    """Synchronous TestClient (ASGI transport, no real network)."""
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture(autouse=True)
def _clear_sessions() -> Generator[None, None, None]:
    """Isolate each test — wipe the in-memory session store before and after."""
    _sessions.clear()
    yield
    _sessions.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UID = "test-user-abc"


def _start_session(client: TestClient, uid: str = UID) -> str:
    """POST /session/start and return the session_id."""
    resp = client.post("/session/start", json={"uid": uid})
    assert resp.status_code == 200
    return resp.json()["session_id"]


def _advance_to_quiz(client: TestClient, session_id: str) -> None:
    """GET /session/{id}/lesson to advance phase from 'lesson' → 'quiz'."""
    resp = client.get(f"/session/{session_id}/lesson")
    assert resp.status_code == 200


def _set_phase(session_id: str, phase: str) -> None:
    """Directly mutate session phase to set up a specific test precondition."""
    _sessions[session_id].phase = phase


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


class TestHealth:
    def test_health_returns_ok(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# POST /session/start
# ---------------------------------------------------------------------------


class TestSessionStart:
    def test_returns_200_with_expected_fields(self, client: TestClient) -> None:
        resp = client.post("/session/start", json={"uid": UID})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "session_id" in body
        assert body["lesson_id"] == "L01"
        assert body["tier"] == "beginner"
        assert body["character_id"] == "tux_jr"

    def test_session_stored_in_memory(self, client: TestClient) -> None:
        resp = client.post("/session/start", json={"uid": UID})
        sid = resp.json()["session_id"]
        assert sid in _sessions
        assert _sessions[sid].uid == UID
        assert _sessions[sid].phase == "lesson"

    def test_two_starts_produce_distinct_session_ids(self, client: TestClient) -> None:
        s1 = client.post("/session/start", json={"uid": UID}).json()["session_id"]
        s2 = client.post("/session/start", json={"uid": UID}).json()["session_id"]
        assert s1 != s2

    def test_missing_uid_returns_422(self, client: TestClient) -> None:
        resp = client.post("/session/start", json={})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /session/{id}/lesson
# ---------------------------------------------------------------------------


class TestGetLesson:
    def test_returns_lesson_response(self, client: TestClient) -> None:
        sid = _start_session(client)
        resp = client.get(f"/session/{sid}/lesson")
        assert resp.status_code == 200
        body = resp.json()
        assert "lesson_text" in body
        assert "character_emotion_state" in body
        assert isinstance(body["key_concepts"], list)

    def test_advances_phase_to_quiz(self, client: TestClient) -> None:
        sid = _start_session(client)
        client.get(f"/session/{sid}/lesson")
        assert _sessions[sid].phase == "quiz"

    def test_wrong_phase_returns_409(self, client: TestClient) -> None:
        sid = _start_session(client)
        _set_phase(sid, "quiz")
        resp = client.get(f"/session/{sid}/lesson")
        assert resp.status_code == 409
        assert "lesson" in resp.json()["detail"]

    def test_unknown_session_returns_404(self, client: TestClient) -> None:
        resp = client.get("/session/no-such-id/lesson")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /session/{id}/quiz/question
# ---------------------------------------------------------------------------


class TestGetQuizQuestion:
    def test_returns_question_response(self, client: TestClient) -> None:
        sid = _start_session(client)
        _advance_to_quiz(client, sid)
        resp = client.get(f"/session/{sid}/quiz/question")
        assert resp.status_code == 200
        body = resp.json()
        assert "question_text" in body
        assert "format" in body
        assert isinstance(body["options"], list)
        assert "character_emotion_state" in body

    def test_increments_questions_asked(self, client: TestClient) -> None:
        sid = _start_session(client)
        _advance_to_quiz(client, sid)
        assert _sessions[sid].quiz_questions_asked == 0
        client.get(f"/session/{sid}/quiz/question")
        assert _sessions[sid].quiz_questions_asked == 1
        client.get(f"/session/{sid}/quiz/question")
        assert _sessions[sid].quiz_questions_asked == 2

    def test_allowed_from_help_phase(self, client: TestClient) -> None:
        """quiz/question is reachable from both 'quiz' and 'help' phases."""
        sid = _start_session(client)
        _advance_to_quiz(client, sid)
        _set_phase(sid, "help")
        resp = client.get(f"/session/{sid}/quiz/question")
        assert resp.status_code == 200

    def test_wrong_phase_returns_409(self, client: TestClient) -> None:
        sid = _start_session(client)
        # Still in 'lesson' phase
        resp = client.get(f"/session/{sid}/quiz/question")
        assert resp.status_code == 409

    def test_unknown_session_returns_404(self, client: TestClient) -> None:
        resp = client.get("/session/no-such-id/quiz/question")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /session/{id}/quiz/answer
# ---------------------------------------------------------------------------


class TestSubmitQuizAnswer:
    def test_returns_answer_response(self, client: TestClient) -> None:
        sid = _start_session(client)
        _advance_to_quiz(client, sid)
        resp = client.post(f"/session/{sid}/quiz/answer", json={"answer": "A"})
        assert resp.status_code == 200
        body = resp.json()
        assert "correct" in body
        assert "explanation" in body
        assert "concept_score_delta" in body
        assert "character_emotion_state" in body
        assert "trigger_help" in body

    def test_wrong_phase_returns_409(self, client: TestClient) -> None:
        sid = _start_session(client)
        resp = client.post(f"/session/{sid}/quiz/answer", json={"answer": "A"})
        assert resp.status_code == 409

    def test_missing_answer_returns_422(self, client: TestClient) -> None:
        sid = _start_session(client)
        _advance_to_quiz(client, sid)
        resp = client.post(f"/session/{sid}/quiz/answer", json={})
        assert resp.status_code == 422

    def test_unknown_session_returns_404(self, client: TestClient) -> None:
        resp = client.post("/session/no-such-id/quiz/answer", json={"answer": "A"})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /session/{id}/help
# ---------------------------------------------------------------------------


class TestHelpTurn:
    def test_first_turn_returns_help_response(self, client: TestClient) -> None:
        sid = _start_session(client)
        _advance_to_quiz(client, sid)
        _set_phase(sid, "help")
        resp = client.post(f"/session/{sid}/help", json={"message": "I don't understand"})
        assert resp.status_code == 200
        body = resp.json()
        assert "resolved" in body
        assert "turns_remaining" in body
        assert body["turns_remaining"] == 2

    def test_three_turns_exhausts_help_cap(self, client: TestClient) -> None:
        sid = _start_session(client)
        _advance_to_quiz(client, sid)
        _set_phase(sid, "help")
        for expected_remaining in (2, 1, 0):
            resp = client.post(f"/session/{sid}/help", json={"message": "help"})
            assert resp.status_code == 200
            assert resp.json()["turns_remaining"] == expected_remaining

    def test_fourth_turn_returns_409(self, client: TestClient) -> None:
        """After the 3-turn cap the session reverts to quiz phase, so any
        further help request is rejected with 409 (wrong phase)."""
        sid = _start_session(client)
        _advance_to_quiz(client, sid)
        _set_phase(sid, "help")
        for _ in range(3):
            client.post(f"/session/{sid}/help", json={"message": "help"})
        # Phase is now 'quiz'; help is no longer accepted
        resp = client.post(f"/session/{sid}/help", json={"message": "one more"})
        assert resp.status_code == 409

    def test_cap_enforced_when_phase_not_reset(self, client: TestClient) -> None:
        """If phase is forced back to 'help' after 3 turns, the counter guard
        (help_turn_count >= 3) fires and returns the explicit cap error."""
        sid = _start_session(client)
        _advance_to_quiz(client, sid)
        _set_phase(sid, "help")
        for _ in range(3):
            client.post(f"/session/{sid}/help", json={"message": "help"})
        # Manually force phase back to 'help' to test the counter guard directly
        _set_phase(sid, "help")
        resp = client.post(f"/session/{sid}/help", json={"message": "one more"})
        assert resp.status_code == 409
        assert "3/3" in resp.json()["detail"]

    def test_after_three_turns_phase_reverts_to_quiz(self, client: TestClient) -> None:
        """After the cap is reached the session returns to quiz phase."""
        sid = _start_session(client)
        _advance_to_quiz(client, sid)
        _set_phase(sid, "help")
        for _ in range(3):
            client.post(f"/session/{sid}/help", json={"message": "help"})
        assert _sessions[sid].phase == "quiz"

    def test_wrong_phase_returns_409(self, client: TestClient) -> None:
        sid = _start_session(client)
        _advance_to_quiz(client, sid)
        # Phase is 'quiz', not 'help'
        resp = client.post(f"/session/{sid}/help", json={"message": "help"})
        assert resp.status_code == 409

    def test_unknown_session_returns_404(self, client: TestClient) -> None:
        resp = client.post("/session/no-such-id/help", json={"message": "help"})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /session/{id}/complete
# ---------------------------------------------------------------------------


class TestCompleteSession:
    def test_returns_summary_with_expected_keys(self, client: TestClient) -> None:
        sid = _start_session(client)
        resp = client.post(f"/session/{sid}/complete", json={"time_on_task_seconds": 120})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        summary = body["summary"]
        for key in (
            "lesson_id",
            "tier_used",
            "quiz_questions_asked",
            "quiz_correct",
            "time_on_task_seconds",
            "help_triggered",
            "gemini_handoff_used",
            "summary_text",
        ):
            assert key in summary, f"Missing summary key: {key}"

    def test_session_removed_from_store_after_complete(self, client: TestClient) -> None:
        sid = _start_session(client)
        client.post(f"/session/{sid}/complete", json={})
        assert sid not in _sessions

    def test_explicit_time_on_task_is_passed_through(self, client: TestClient) -> None:
        sid = _start_session(client)
        resp = client.post(f"/session/{sid}/complete", json={"time_on_task_seconds": 300})
        assert resp.json()["summary"]["time_on_task_seconds"] == 300

    def test_zero_time_on_task_falls_back_to_elapsed(self, client: TestClient) -> None:
        """When time_on_task_seconds=0 the summary uses the elapsed wall-clock time."""
        sid = _start_session(client)
        resp = client.post(f"/session/{sid}/complete", json={"time_on_task_seconds": 0})
        assert resp.json()["summary"]["time_on_task_seconds"] >= 0

    def test_help_triggered_flag_reflects_help_usage(self, client: TestClient) -> None:
        sid = _start_session(client)
        _advance_to_quiz(client, sid)
        _set_phase(sid, "help")
        client.post(f"/session/{sid}/help", json={"message": "help"})
        _set_phase(sid, "quiz")
        resp = client.post(f"/session/{sid}/complete", json={})
        assert resp.json()["summary"]["help_triggered"] is True

    def test_gemini_handoff_used_true_after_three_help_turns(self, client: TestClient) -> None:
        sid = _start_session(client)
        _advance_to_quiz(client, sid)
        _set_phase(sid, "help")
        for _ in range(3):
            client.post(f"/session/{sid}/help", json={"message": "help"})
        resp = client.post(f"/session/{sid}/complete", json={})
        assert resp.json()["summary"]["gemini_handoff_used"] is True

    def test_quiz_stats_reflected_in_summary(self, client: TestClient) -> None:
        sid = _start_session(client)
        _advance_to_quiz(client, sid)
        # Ask 2 questions
        client.get(f"/session/{sid}/quiz/question")
        client.get(f"/session/{sid}/quiz/question")
        resp = client.post(f"/session/{sid}/complete", json={})
        assert resp.json()["summary"]["quiz_questions_asked"] == 2

    def test_second_complete_returns_404(self, client: TestClient) -> None:
        """Once a session is completed it must no longer exist."""
        sid = _start_session(client)
        client.post(f"/session/{sid}/complete", json={})
        resp = client.post(f"/session/{sid}/complete", json={})
        assert resp.status_code == 404

    def test_unknown_session_returns_404(self, client: TestClient) -> None:
        resp = client.post("/session/no-such-id/complete", json={})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Full happy-path lifecycle
# ---------------------------------------------------------------------------


class TestFullSessionLifecycle:
    def test_start_lesson_quiz_complete(self, client: TestClient) -> None:
        """Golden path: start → lesson → quiz question → quiz answer → complete."""
        # 1. Start
        start_resp = client.post("/session/start", json={"uid": UID})
        assert start_resp.status_code == 200
        sid = start_resp.json()["session_id"]

        # 2. Lesson
        lesson_resp = client.get(f"/session/{sid}/lesson")
        assert lesson_resp.status_code == 200

        # 3. Quiz question
        q_resp = client.get(f"/session/{sid}/quiz/question")
        assert q_resp.status_code == 200

        # 4. Quiz answer
        a_resp = client.post(f"/session/{sid}/quiz/answer", json={"answer": "B"})
        assert a_resp.status_code == 200

        # 5. Complete
        c_resp = client.post(f"/session/{sid}/complete", json={"time_on_task_seconds": 60})
        assert c_resp.status_code == 200
        assert c_resp.json()["status"] == "ok"
        assert sid not in _sessions
