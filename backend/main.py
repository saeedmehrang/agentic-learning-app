"""
FastAPI entrypoint for the ADK learning backend.

Session lifecycle
-----------------
1. POST /session/start  → creates an InMemorySession, runs the full 4-agent
   pipeline via Runner.run_async, collects the final event, and returns
   SessionStartResponse.

HelpAgent conditional routing
------------------------------
HelpAgent is only invoked when LessonAgent emits ``trigger_help: true`` in its
session state (``lesson_output``). The pipeline SequentialAgent carries all 4
sub_agents, but main.py skips the HelpAgent step when trigger_help is absent or
false by running a two-phase approach:

  Phase A — ContextAgent + LessonAgent (via a sub-pipeline or direct runner call)
  Phase B — HelpAgent only when trigger_help is true (via HelpAgentRunner)
  Phase C — SummaryAgent always

For the initial implementation we run the full SequentialAgent pipeline and rely
on the HelpAgent's system prompt + HelpAgentRunner to handle the conditional
gracefully. A stricter split can be added in Phase 5 once session state routing
is fully mapped out.
"""
from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from fastapi import FastAPI, HTTPException
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types as genai_types
from pydantic import BaseModel

from config import settings
from logging_config import configure_logging
from pipeline import pipeline

configure_logging()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ADK runner + session service — created once at module startup
# ---------------------------------------------------------------------------

_session_service = InMemorySessionService()

_runner = Runner(
    agent=pipeline,
    app_name="agentic_learning_app",
    session_service=_session_service,
)

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("Backend starting", extra={"app_env": settings.app_env})
    yield
    logger.info("Backend shutting down")


app = FastAPI(title="Agentic Learning Backend", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint — required by Cloud Run."""
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class SessionStartRequest(BaseModel):
    uid: str


class SessionStartResponse(BaseModel):
    status: str
    session_id: str


# ---------------------------------------------------------------------------
# Session start endpoint
# ---------------------------------------------------------------------------


@app.post("/session/start", response_model=SessionStartResponse)
async def session_start(request: SessionStartRequest) -> SessionStartResponse:
    """
    Start a new learning session for the given learner UID.

    Steps:
    1. Create an in-memory session keyed by the learner UID.
    2. Build a user Content message carrying the UID so agents can call their
       tools (e.g. read_learner_context).
    3. Run the pipeline via Runner.run_async and drain the event stream.
    4. Return the session_id so the Flutter client can reference it in follow-up
       calls.

    The runner executes ContextAgent → LessonAgent → HelpAgent (conditional) →
    SummaryAgent. HelpAgent is only meaningfully active when LessonAgent sets
    trigger_help: true in session state; otherwise it passes through without
    generating substantive output.
    """
    try:
        # Step 1 — create session
        # TODO: verify ADK Runner API — InMemorySessionService.create_session is
        # synchronous in the current SDK version; switch to await if it becomes async.
        session = _session_service.create_session(
            app_name="agentic_learning_app",
            user_id=request.uid,
        )
        session_id: str = session.id

        logger.info(
            "Session created",
            extra={"uid": request.uid, "session_id": session_id},
        )

        # Step 2 — build the initial user message
        # The UID is passed as text so ContextAgent can extract it and call
        # read_learner_context(uid=...).
        new_message = genai_types.Content(
            role="user",
            parts=[genai_types.Part(text=request.uid)],
        )

        # Step 3 — run pipeline and drain events
        final_event: Any = None
        # TODO: verify ADK Runner API — run_async returns an AsyncGenerator[Event, None]
        async for event in _runner.run_async(
            user_id=request.uid,
            session_id=session_id,
            new_message=new_message,
        ):
            if event.is_final_response():
                final_event = event

        if final_event is not None:
            _log_pipeline_completion(session_id=session_id, event=final_event)

        return SessionStartResponse(status="ok", session_id=session_id)

    except Exception as exc:
        logger.error(
            "Session start failed",
            extra={"uid": request.uid, "error": str(exc)},
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Session initialisation failed") from exc


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _log_pipeline_completion(*, session_id: str, event: Any) -> None:
    """
    Log the final pipeline event at INFO level.

    Privacy constraint: never log gemini_handoff_prompt content — log the boolean
    gemini_handoff_used only. This function inspects summary_output if present and
    strips any handoff prompt text before logging.
    """
    try:
        content = event.content
        if content is None:
            return
        # Extract text from the first part if available
        parts = getattr(content, "parts", []) or []
        if not parts:
            return
        raw_text: str = getattr(parts[0], "text", "") or ""
        if not raw_text:
            return

        # Parse JSON to enforce privacy — log only safe scalar fields
        try:
            parsed: dict[str, Any] = json.loads(raw_text)
        except (json.JSONDecodeError, ValueError):
            # Non-JSON final event (e.g. plain text from an agent); log length only
            logger.info(
                "Pipeline completed (non-JSON final event)",
                extra={"session_id": session_id, "output_length": len(raw_text)},
            )
            return

        # Strip gemini_handoff_prompt — log only the boolean flag
        safe: dict[str, Any] = {
            k: v
            for k, v in parsed.items()
            if k != "gemini_handoff_prompt"
        }
        logger.info(
            "Pipeline completed",
            extra={"session_id": session_id, "summary": safe},
        )

    except Exception:
        # Never let logging failures surface to the caller
        logger.warning(
            "Could not log pipeline completion event",
            extra={"session_id": session_id},
        )
