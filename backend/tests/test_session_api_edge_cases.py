"""
Edge-case tests for the session API HTTP layer.

Covers input validation, concurrent session isolation, and boundary
conditions not exercised by the main test_session_api.py suite.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from main import _sessions, app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client() -> TestClient:
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
    _sessions[sid].phase = phase


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
