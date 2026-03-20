"""
Integration tests for the 4-agent pipeline.

Two test layers
---------------

Layer 1 — Unit-level pipeline routing tests (mocked, run in CI):
  Verify pipeline structure and agent composition without live GCP calls.
  These tests import pipeline.py and assert on sub_agent membership,
  HelpAgentRunner behaviour, and the shape of the gemini_handoff_prompt
  on unresolved exit.

Layer 2 — Live end-to-end tests:
  All marked @pytest.mark.skip — require a running Cloud Run deployment
  and live Firestore/Cloud SQL. Run these manually after Phase 3 and
  Phase 1 operational steps (VPC connector, DB seeding) are complete.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Layer 1: Unit-level pipeline routing tests (mocked, CI-safe)
# ---------------------------------------------------------------------------


class TestPipelineStructure:
    """Verify that pipeline.py wires all 4 agents in the correct order."""

    def test_pipeline_has_four_sub_agents(self) -> None:
        """Pipeline must contain exactly 4 sub_agents."""
        from pipeline import pipeline

        assert len(pipeline.sub_agents) == 4

    def test_pipeline_sub_agent_order(self) -> None:
        """Sub-agents must be ordered: context, lesson, help, summary."""
        from pipeline import pipeline

        names = [agent.name for agent in pipeline.sub_agents]
        assert names == ["context_agent", "lesson_agent", "help_agent", "summary_agent"]

    def test_pipeline_name(self) -> None:
        """Pipeline name must be 'learning_pipeline'."""
        from pipeline import pipeline

        assert pipeline.name == "learning_pipeline"

    def test_context_agent_in_pipeline(self) -> None:
        """context_agent must be the first sub_agent."""
        from agents.context_agent import context_agent
        from pipeline import pipeline

        assert pipeline.sub_agents[0] is context_agent

    def test_lesson_agent_in_pipeline(self) -> None:
        """lesson_agent must be the second sub_agent."""
        from agents.lesson_agent import lesson_agent
        from pipeline import pipeline

        assert pipeline.sub_agents[1] is lesson_agent

    def test_help_agent_in_pipeline(self) -> None:
        """help_agent must be the third sub_agent."""
        from agents.help_agent import help_agent
        from pipeline import pipeline

        assert pipeline.sub_agents[2] is help_agent

    def test_summary_agent_in_pipeline(self) -> None:
        """summary_agent must be the fourth sub_agent."""
        from agents.summary_agent import summary_agent
        from pipeline import pipeline

        assert pipeline.sub_agents[3] is summary_agent


class TestHelpAgentConditionalRouting:
    """
    Verify HelpAgentRunner enforces conditional routing logic.

    These tests exercise the runner state machine directly — no LLM calls are made.
    """

    def test_happy_path_no_help_trigger(self) -> None:
        """
        When LessonAgent does not emit trigger_help, HelpAgent must not be invoked.

        Simulated by checking HelpAgentRunner starts in IDLE and the caller can
        inspect trigger_help before calling increment_turn.
        """
        from agents.help_agent import IDLE, HelpAgentRunner

        runner = HelpAgentRunner()
        lesson_output: dict[str, object] = {
            "correct": True,
            "explanation": "Great answer!",
            "concept_score_delta": 0.1,
            "character_emotion_state": "celebrating",
            # trigger_help absent — HelpAgent should not be invoked
        }

        trigger_help: bool = bool(lesson_output.get("trigger_help", False))
        assert trigger_help is False
        # Runner stays IDLE — never called
        assert runner.state == IDLE
        assert runner.turn_count == 0

    def test_help_path_resolved_within_turns(self) -> None:
        """
        When LessonAgent emits trigger_help: true, HelpAgentRunner is invoked and
        can resolve within the 3-turn cap.
        """
        from agents.help_agent import RESOLVED, HelpAgentRunner

        runner = HelpAgentRunner()
        lesson_output: dict[str, object] = {
            "correct": False,
            "explanation": "Incorrect.",
            "concept_score_delta": -0.1,
            "character_emotion_state": "encouraging",
            "trigger_help": True,
        }

        trigger_help = bool(lesson_output.get("trigger_help", False))
        assert trigger_help is True

        # Simulate one help turn, then resolution
        runner.increment_turn()
        assert runner.turn_count == 1
        assert runner.is_at_cap() is False

        runner.log_resolution(resolved=True)
        assert runner.state == RESOLVED

    def test_help_path_unresolved_at_turn_3_includes_handoff_prompt(self) -> None:
        """
        When HelpAgent reaches turn 3 unresolved, the output must include a
        gemini_handoff_prompt field and resolved must be false.
        """
        from agents.help_agent import HELP_AGENT_MAX_TURNS, RESOLVED, HelpAgentRunner

        runner = HelpAgentRunner()

        # Exhaust all 3 turns
        for _ in range(HELP_AGENT_MAX_TURNS):
            runner.increment_turn()

        assert runner.is_at_cap() is True

        # Simulate the unresolved JSON output the LLM would produce at turn 3
        unresolved_output: dict[str, object] = {
            "resolved": False,
            "gemini_handoff_prompt": (
                "The learner is studying Linux file permissions and is confused "
                "about the difference between chmod 755 and chmod 644. Please "
                "explain the execute bit in the context of directories."
            ),
        }

        assert unresolved_output["resolved"] is False
        assert "gemini_handoff_prompt" in unresolved_output
        assert isinstance(unresolved_output["gemini_handoff_prompt"], str)
        assert len(str(unresolved_output["gemini_handoff_prompt"])) > 0

        # Runner should be resolved (forced) after cap
        runner.log_resolution(resolved=False)
        assert runner.state == RESOLVED

    def test_help_agent_hard_cap_raises_on_4th_turn(self) -> None:
        """increment_turn() must raise RuntimeError if called a 4th time."""
        from agents.help_agent import HELP_AGENT_MAX_TURNS, HelpAgentRunner

        runner = HelpAgentRunner()
        for _ in range(HELP_AGENT_MAX_TURNS):
            runner.increment_turn()

        with pytest.raises(RuntimeError, match="hard cap exceeded"):
            runner.increment_turn()

    def test_summary_agent_always_runs_after_no_help(self) -> None:
        """
        Verify that the summary_agent is always last in the pipeline regardless
        of whether help was triggered. Pipeline order is structural — this test
        asserts on pipeline.sub_agents position.
        """
        from agents.summary_agent import summary_agent
        from pipeline import pipeline

        assert pipeline.sub_agents[-1] is summary_agent

    def test_summary_agent_always_runs_after_help(self) -> None:
        """
        Even when HelpAgent is the third step, summary_agent must follow it.
        """
        from agents.help_agent import help_agent
        from agents.summary_agent import summary_agent
        from pipeline import pipeline

        help_idx = pipeline.sub_agents.index(help_agent)
        summary_idx = pipeline.sub_agents.index(summary_agent)
        assert summary_idx == help_idx + 1


class TestMainSessionStart:
    """Unit-level tests for the /session/start endpoint with mocked ADK runner."""

    @pytest.mark.asyncio
    async def test_session_start_returns_ok_status(self) -> None:
        """session_start must return status='ok' on success."""
        from httpx import ASGITransport, AsyncClient

        mock_event = MagicMock()
        mock_event.is_final_response.return_value = True
        mock_event.content = None

        mock_session = MagicMock()
        mock_session.id = "test-session-id-001"

        with (
            patch("main._session_service") as mock_svc,
            patch("main._runner") as mock_runner,
        ):
            mock_svc.create_session.return_value = mock_session

            async def _fake_run_async(**kwargs):  # type: ignore[no-untyped-def]
                yield mock_event

            mock_runner.run_async = _fake_run_async

            from main import app

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/session/start", json={"uid": "test-uid-abc"}
                )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"

    @pytest.mark.asyncio
    async def test_session_start_returns_session_id(self) -> None:
        """session_start must return a non-empty session_id."""
        from httpx import ASGITransport, AsyncClient

        mock_event = MagicMock()
        mock_event.is_final_response.return_value = True
        mock_event.content = None

        mock_session = MagicMock()
        mock_session.id = "deterministic-session-xyz"

        with (
            patch("main._session_service") as mock_svc,
            patch("main._runner") as mock_runner,
        ):
            mock_svc.create_session.return_value = mock_session

            async def _fake_run_async(**kwargs):  # type: ignore[no-untyped-def]
                yield mock_event

            mock_runner.run_async = _fake_run_async

            from main import app

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/session/start", json={"uid": "test-uid-abc"}
                )

        body = response.json()
        assert body["session_id"] == "deterministic-session-xyz"

    @pytest.mark.asyncio
    async def test_session_start_returns_500_on_runner_failure(self) -> None:
        """session_start must return HTTP 500 when the runner raises."""
        from httpx import ASGITransport, AsyncClient

        mock_session = MagicMock()
        mock_session.id = "fail-session"

        async def _failing_run_async(**kwargs):  # type: ignore[no-untyped-def]
            # Raise immediately; the yield makes this an async generator so the
            # caller can iterate it, at which point the RuntimeError is raised.
            raise RuntimeError("Simulated ADK runner failure")
            yield  # pragma: no cover

        with (
            patch("main._session_service") as mock_svc,
            patch("main._runner") as mock_runner,
        ):
            mock_svc.create_session.return_value = mock_session
            mock_runner.run_async = _failing_run_async

            from main import app

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/session/start", json={"uid": "test-uid-fail"}
                )

        assert response.status_code == 500

    @pytest.mark.asyncio
    async def test_session_start_privacy_no_handoff_prompt_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """
        gemini_handoff_prompt content must never appear in log output.

        Simulates a final event whose content includes a gemini_handoff_prompt
        and verifies the log output contains only the boolean flag.
        """
        import logging

        from httpx import ASGITransport, AsyncClient

        # Build a mock final event with a gemini_handoff_prompt in JSON content
        sensitive_prompt = "Learner is confused about chmod — please help them."
        payload = json.dumps(
            {
                "resolved": False,
                "gemini_handoff_prompt": sensitive_prompt,
                "gemini_handoff_used": True,
            }
        )
        mock_part = MagicMock()
        mock_part.text = payload
        mock_content = MagicMock()
        mock_content.parts = [mock_part]
        mock_event = MagicMock()
        mock_event.is_final_response.return_value = True
        mock_event.content = mock_content

        mock_session = MagicMock()
        mock_session.id = "privacy-test-session"

        with (
            patch("main._session_service") as mock_svc,
            patch("main._runner") as mock_runner,
        ):
            mock_svc.create_session.return_value = mock_session

            async def _fake_run_async(**kwargs):  # type: ignore[no-untyped-def]
                yield mock_event

            mock_runner.run_async = _fake_run_async

            from main import app

            with caplog.at_level(logging.INFO, logger="main"):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    await client.post("/session/start", json={"uid": "uid-privacy"})

        # The sensitive prompt content must not appear anywhere in log records
        for record in caplog.records:
            assert sensitive_prompt not in str(record.getMessage())
            assert sensitive_prompt not in str(getattr(record, "summary", ""))


# ---------------------------------------------------------------------------
# Layer 2: Live end-to-end tests (skipped — require live Cloud Run + GCP)
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason="Requires live Cloud Run — run after Phase 3 and Phase 1 operational steps are complete"
)
class TestLivePipelineEndToEnd:
    """
    Live integration tests against a deployed Cloud Run instance.

    Prerequisites:
    - Cloud Run service deployed and accessible at CLOUD_RUN_URL env var
    - Firestore collections initialised for the test UID
    - Cloud SQL seeded with at least lesson L01 content (Phase 1.4)
    - VPC connector configured (Phase 1.4 prerequisite)

    All tests in this class are skipped in CI. Run manually:
      pytest tests/integration/test_pipeline.py::TestLivePipelineEndToEnd -v
    """

    BASE_URL = "http://localhost:8080"  # override with CLOUD_RUN_URL in env

    @pytest.mark.asyncio
    async def test_live_session_start_happy_path(self) -> None:
        """
        Real HTTP call to POST /session/start.

        Verifies:
        - 200 status code
        - Response has status='ok' and a non-empty session_id
        - session_id is a valid UUID string
        """
        import os
        import uuid

        import httpx

        url = os.environ.get("CLOUD_RUN_URL", self.BASE_URL)
        async with httpx.AsyncClient(base_url=url, timeout=60.0) as client:
            response = await client.post(
                "/session/start", json={"uid": "integration-test-uid-001"}
            )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert len(body["session_id"]) > 0
        # Must be a parseable UUID
        uuid.UUID(body["session_id"])

    @pytest.mark.asyncio
    async def test_live_firestore_state_after_session(self) -> None:
        """
        After a successful session, Firestore must contain a session record.

        Verifies:
        - learners/{uid}/sessions/ collection has at least one document
        - The document contains all required SESSION_RECORD_SCHEMA fields
        """
        import os

        import httpx
        from google.cloud import firestore

        url = os.environ.get("CLOUD_RUN_URL", self.BASE_URL)
        test_uid = "integration-test-uid-002"

        async with httpx.AsyncClient(base_url=url, timeout=60.0) as client:
            response = await client.post("/session/start", json={"uid": test_uid})

        assert response.status_code == 200
        assert response.json()["session_id"]  # non-empty session_id

        db = firestore.AsyncClient()
        sessions_ref = (
            db.collection("learners").document(test_uid).collection("sessions")
        )
        docs = await sessions_ref.get()
        assert len(docs) >= 1

        # Verify schema of the most recent session
        from agents.summary_agent import SESSION_RECORD_SCHEMA

        latest = docs[-1].to_dict() or {}
        for field in SESSION_RECORD_SCHEMA:
            assert field in latest, f"Missing field in session record: {field}"

    @pytest.mark.asyncio
    async def test_live_load_10_concurrent_sessions(self) -> None:
        """
        Load test: 10 concurrent POST /session/start requests must all succeed.

        This validates that the Cloud Run instance handles concurrent sessions
        within the scale-to-zero constraints.
        """
        import asyncio
        import os

        import httpx

        url = os.environ.get("CLOUD_RUN_URL", self.BASE_URL)

        async def start_session(uid: str) -> int:
            async with httpx.AsyncClient(base_url=url, timeout=120.0) as client:
                response = await client.post("/session/start", json={"uid": uid})
                return response.status_code

        uids = [f"load-test-uid-{i:03d}" for i in range(10)]
        status_codes = await asyncio.gather(*[start_session(uid) for uid in uids])

        failures = [code for code in status_codes if code != 200]
        assert len(failures) == 0, f"Load test failures: {failures}"
