"""
FastAPI entrypoint for the ADK learning backend.

Session lifecycle (per-turn interactive API)
--------------------------------------------
1. POST  /session/start          → ContextAgent only. Returns session_id + context_output.
2. GET   /session/{id}/lesson    → LessonAgent teaching phase (Turn 1). Returns lesson JSON.
3. POST  /session/{id}/quiz/answer → LessonAgent quiz evaluation (Turns 2–N). Returns answer eval.
4. POST  /session/{id}/help      → HelpAgent (max 3 turns, hard-capped). Returns help response.
5. POST  /session/{id}/complete  → SummaryAgent. Writes Firestore. Returns summary JSON.

Session service isolation:
- ContextAgent: own service (_context_session_service) — single-turn, no history needed.
- LessonAgent + HelpAgent: shared service (_lesson_session_service) — HelpAgent needs
  lesson context to answer follow-up questions.
- SummaryAgent: own service (_summary_session_service) — single-turn, sending the full
  lesson history would exhaust token quotas and is unnecessary.

HelpAgent hard cap
------------------
HelpAgentRunner (imported from agents.help_agent) enforces the 3-turn limit in code.
The /session/{id}/help endpoint checks is_at_cap() before calling the runner.
"""
from __future__ import annotations

import json
import logging
import os
import time as _time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator

from fastapi import FastAPI, HTTPException
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types as genai_types
from opentelemetry import trace as otel_trace
from opentelemetry.exporter.gcp_trace import CloudTraceSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.propagate import set_global_textmap
from opentelemetry.propagators.cloud_trace_propagator import CloudTraceFormatPropagator
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from pydantic import BaseModel

from agents.context_agent import context_agent
from agents.help_agent import HelpAgentRunner, help_agent
from agents.lesson_agent import lesson_agent
from agents.summary_agent import summary_agent
from config import settings
from logging_config import configure_logging

# ADK / google-genai SDK reads these from os.environ directly (not pydantic settings).
# Set them here so that .env values are honoured even when not exported to the shell.
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "1")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", settings.gcp_project_id)
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", settings.gcp_location)

configure_logging()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# OpenTelemetry — Cloud Trace
# Spans are exported to Cloud Trace (free within GCP quotas).
# ADK emits child spans automatically: call_llm, execute_tool, invoke_agent.
# APP_VERSION is injected at deploy time via --set-env-vars APP_VERSION=$COMMIT_SHA
# so each Cloud Run revision (= squash-merge to main) is a separate series in
# Cloud Monitoring dashboards for before/after latency comparison.
# ---------------------------------------------------------------------------

_otel_resource = Resource(attributes={
    "service.name": "agentic-learning-backend",
    "service.version": settings.app_version,
})
_tracer_provider = TracerProvider(resource=_otel_resource)
_tracer_provider.add_span_processor(BatchSpanProcessor(CloudTraceSpanExporter()))
otel_trace.set_tracer_provider(_tracer_provider)
set_global_textmap(CloudTraceFormatPropagator())

_tracer = otel_trace.get_tracer("agentic_learning")

# ---------------------------------------------------------------------------
# ADK session service — shared across all per-agent runners so that output_key
# values written by one agent are visible to the next agent in the same session.
# ---------------------------------------------------------------------------

_context_session_service = InMemorySessionService()
_lesson_session_service = InMemorySessionService()   # shared by lesson + help
_summary_session_service = InMemorySessionService()

_context_runner = Runner(
    agent=context_agent,
    app_name="agentic_learning_app",
    session_service=_context_session_service,
)
_lesson_runner = Runner(
    agent=lesson_agent,
    app_name="agentic_learning_app",
    session_service=_lesson_session_service,
)
_help_runner = Runner(
    agent=help_agent,
    app_name="agentic_learning_app",
    session_service=_lesson_session_service,
)
_summary_runner = Runner(
    agent=summary_agent,
    app_name="agentic_learning_app",
    session_service=_summary_session_service,
)


# ---------------------------------------------------------------------------
# In-memory session store (dev/interactive — not for prod scale-out)
# Keyed by session_id. Holds inter-turn data the HTTP layer needs.
# ---------------------------------------------------------------------------


@dataclass
class SessionData:
    session_id: str
    uid: str
    adk_session_id: str          # lesson+help session (shared between those two agents)
    summary_adk_session_id: str  # isolated session for SummaryAgent
    context_output: dict[str, Any] = field(default_factory=dict)
    lesson_output: dict[str, Any] = field(default_factory=dict)
    quiz_questions_asked: int = 0
    quiz_correct: int = 0        # running count of correct answers this session
    session_start_ts: float = field(default_factory=lambda: __import__("time").time())
    help_runner: HelpAgentRunner = field(default_factory=HelpAgentRunner)
    phase: str = "context"       # context | lesson | quiz | help | complete


_sessions: dict[str, SessionData] = {}


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("Backend starting", extra={"app_env": settings.app_env})
    yield
    logger.info("Backend shutting down")


app = FastAPI(title="Agentic Learning Backend", lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


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
    context_output: dict[str, Any]


class LessonResponse(BaseModel):
    lesson_text: str
    character_emotion_state: str
    key_concepts: list[str]


class QuizQuestionResponse(BaseModel):
    question_text: str
    format: str
    options: list[str]
    character_emotion_state: str


class QuizAnswerRequest(BaseModel):
    answer: str


class QuizAnswerResponse(BaseModel):
    correct: bool
    explanation: str
    concept_score_delta: float
    character_emotion_state: str
    trigger_help: bool = False


class HelpRequest(BaseModel):
    message: str


class HelpResponse(BaseModel):
    resolved: bool
    character_emotion_state: str | None = None
    gemini_handoff_prompt: str | None = None
    turns_remaining: int = 0


class SessionCompleteResponse(BaseModel):
    status: str
    summary: dict[str, Any]


# ---------------------------------------------------------------------------
# Helper: run a single agent turn and return the last output text
# ---------------------------------------------------------------------------


async def _run_agent_turn(
    runner: Runner,
    uid: str,
    adk_session_id: str,
    message: str,
    *,
    agent_name: str = "unknown",
) -> str:
    """Run one agent turn and return the final response text."""
    with _tracer.start_as_current_span(f"agent_turn.{agent_name}") as span:
        span.set_attribute("agent", agent_name)
        span.set_attribute("app_version", settings.app_version)
        t0 = _time.monotonic()

        new_message = genai_types.Content(
            role="user",
            parts=[genai_types.Part(text=message)],
        )
        final_text = ""
        async for event in runner.run_async(
            user_id=uid,
            session_id=adk_session_id,
            new_message=new_message,
        ):
            if event.is_final_response():
                parts = getattr(event.content, "parts", []) or []
                if parts:
                    final_text = getattr(parts[0], "text", "") or ""

        latency_ms = int((_time.monotonic() - t0) * 1000)
        span.set_attribute("latency_ms", latency_ms)
        logger.info(
            "agent_turn_complete",
            extra={
                "agent": agent_name,
                "latency_ms": latency_ms,
                "app_version": settings.app_version,
            },
        )
        return final_text


def _parse_json_response(raw: str, session_id: str, context: str) -> dict[str, Any]:
    """Parse JSON from agent output; raise HTTPException on failure."""
    text = raw.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]) if len(lines) > 2 else text
    # Try direct parse first
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass
    # Fall back: extract the first {...} JSON object from prose output
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except (json.JSONDecodeError, ValueError):
            pass
    logger.error(
        "Agent returned non-JSON output",
        extra={"session_id": session_id, "context": context, "raw": raw[:500]},
    )
    raise HTTPException(status_code=502, detail=f"Agent output parse error: {context}")


def _get_session(session_id: str) -> SessionData:
    """Retrieve session or raise 404."""
    data = _sessions.get(session_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return data


# ---------------------------------------------------------------------------
# POST /session/start
# ---------------------------------------------------------------------------


@app.post("/session/start", response_model=SessionStartResponse)
async def session_start(request: SessionStartRequest) -> SessionStartResponse:
    """
    Start a new learning session. Runs ContextAgent to determine the next concept.
    Returns session_id and context_output (concept, tier, character, session goal).
    """
    session_id = str(uuid.uuid4())
    try:
        # ContextAgent gets its own isolated session (single-turn, no history needed)
        ctx_adk_session = await _context_session_service.create_session(
            app_name="agentic_learning_app",
            user_id=request.uid,
        )
        # Lesson+Help share a session so HelpAgent can see lesson history
        lesson_adk_session = await _lesson_session_service.create_session(
            app_name="agentic_learning_app",
            user_id=request.uid,
        )
        # SummaryAgent gets its own isolated session (single-turn, avoids token bloat)
        summary_adk_session = await _summary_session_service.create_session(
            app_name="agentic_learning_app",
            user_id=request.uid,
        )

        raw = await _run_agent_turn(
            _context_runner, request.uid, ctx_adk_session.id, request.uid,
            agent_name="context_agent",
        )
        context_output = _parse_json_response(raw, session_id, "context_agent")

        _sessions[session_id] = SessionData(
            session_id=session_id,
            uid=request.uid,
            adk_session_id=lesson_adk_session.id,
            summary_adk_session_id=summary_adk_session.id,
            context_output=context_output,
            phase="lesson",
        )

        logger.info(
            "Session started",
            extra={
                "session_id": session_id,
                "uid": request.uid,
                "concept": context_output.get("next_concept_id"),
                "tier": context_output.get("difficulty_tier"),
            },
        )
        return SessionStartResponse(
            status="ok",
            session_id=session_id,
            context_output=context_output,
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "Session start failed", extra={"uid": request.uid, "error": str(exc)}, exc_info=True
        )
        raise HTTPException(status_code=500, detail="Session initialisation failed") from exc


# ---------------------------------------------------------------------------
# GET /session/{session_id}/lesson
# ---------------------------------------------------------------------------


@app.get("/session/{session_id}/lesson")
async def get_lesson(session_id: str) -> LessonResponse:
    """
    Deliver the lesson for this session (LessonAgent teaching phase, Turn 1).
    Must be called after /session/start. Returns lesson text, emotion state, key concepts.
    """
    data = _get_session(session_id)
    if data.phase != "lesson":
        raise HTTPException(
            status_code=409, detail=f"Session phase is '{data.phase}', expected 'lesson'"
        )

    ctx = data.context_output
    prompt = (
        f"concept_id={ctx.get('next_concept_id')} "
        f"difficulty_tier={ctx.get('difficulty_tier')} "
        f"module_character_id={ctx.get('module_character_id')} "
        f"session_goal={ctx.get('session_goal')}"
    )

    try:
        raw = await _run_agent_turn(
            _lesson_runner, data.uid, data.adk_session_id, prompt,
            agent_name="lesson_agent",
        )
        parsed = _parse_json_response(raw, session_id, "lesson_agent_teaching")
        data.lesson_output = parsed
        data.phase = "quiz"

        return LessonResponse(
            lesson_text=parsed.get("lesson_text", ""),
            character_emotion_state=parsed.get("character_emotion_state", "teaching"),
            key_concepts=parsed.get("key_concepts", []),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "Lesson delivery failed",
            extra={"session_id": session_id, "error": str(exc)},
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Lesson delivery failed") from exc


# ---------------------------------------------------------------------------
# GET /session/{session_id}/quiz/question
# ---------------------------------------------------------------------------


@app.get("/session/{session_id}/quiz/question")
async def get_quiz_question(session_id: str) -> QuizQuestionResponse:
    """
    Request the next quiz question from LessonAgent (quiz phase).
    Must be called after /lesson. Returns one question per call.
    """
    data = _get_session(session_id)
    if data.phase not in ("quiz", "help"):
        raise HTTPException(
            status_code=409, detail=f"Session phase is '{data.phase}', expected 'quiz'"
        )

    try:
        raw = await _run_agent_turn(
            _lesson_runner, data.uid, data.adk_session_id, "next question",
            agent_name="lesson_agent",
        )
        parsed = _parse_json_response(raw, session_id, "lesson_agent_quiz_question")
        data.quiz_questions_asked += 1

        return QuizQuestionResponse(
            question_text=parsed.get("question_text", ""),
            format=parsed.get("format", "mc"),
            options=parsed.get("options", []),
            character_emotion_state=parsed.get("character_emotion_state", "curious"),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "Quiz question failed",
            extra={"session_id": session_id, "error": str(exc)},
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Quiz question failed") from exc


# ---------------------------------------------------------------------------
# POST /session/{session_id}/quiz/answer
# ---------------------------------------------------------------------------


@app.post("/session/{session_id}/quiz/answer")
async def submit_quiz_answer(session_id: str, request: QuizAnswerRequest) -> QuizAnswerResponse:
    """
    Submit a quiz answer to LessonAgent and get evaluation.
    If trigger_help is true in the response, call /help next.
    """
    data = _get_session(session_id)
    if data.phase not in ("quiz", "help"):
        raise HTTPException(
            status_code=409, detail=f"Session phase is '{data.phase}', expected 'quiz'"
        )

    try:
        raw = await _run_agent_turn(
            _lesson_runner, data.uid, data.adk_session_id, request.answer,
            agent_name="lesson_agent",
        )
        parsed = _parse_json_response(raw, session_id, "lesson_agent_answer_eval")

        trigger_help = bool(parsed.get("trigger_help", False))
        if trigger_help:
            data.phase = "help"

        if parsed.get("correct"):
            data.quiz_correct += 1

        return QuizAnswerResponse(
            correct=bool(parsed.get("correct", False)),
            explanation=parsed.get("explanation", ""),
            concept_score_delta=float(parsed.get("concept_score_delta", 0.0)),
            character_emotion_state=parsed.get("character_emotion_state", "encouraging"),
            trigger_help=trigger_help,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "Quiz answer failed",
            extra={"session_id": session_id, "error": str(exc)},
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Quiz answer evaluation failed") from exc


# ---------------------------------------------------------------------------
# POST /session/{session_id}/help
# ---------------------------------------------------------------------------


@app.post("/session/{session_id}/help")
async def help_turn(session_id: str, request: HelpRequest) -> HelpResponse:
    """
    Send a message to HelpAgent. Hard-capped at 3 turns.
    On turn 3 unresolved: returns gemini_handoff_prompt.
    After resolution, session phase returns to 'quiz'.
    """
    data = _get_session(session_id)
    if data.phase != "help":
        raise HTTPException(
            status_code=409, detail=f"Session phase is '{data.phase}', expected 'help'"
        )

    hr = data.help_runner
    if hr.is_at_cap():
        raise HTTPException(status_code=409, detail="HelpAgent turn cap reached (3/3)")

    try:
        hr.increment_turn()
        raw = await _run_agent_turn(
            _help_runner, data.uid, data.adk_session_id, request.message,
            agent_name="help_agent",
        )
        parsed = _parse_json_response(raw, session_id, "help_agent")

        resolved = bool(parsed.get("resolved", False))
        at_cap = hr.is_at_cap()

        if resolved or at_cap:
            hr.log_resolution(resolved=resolved)
            data.phase = "quiz"  # resume quiz after help

        turns_remaining = max(0, 3 - hr.turn_count)

        # Privacy: never log handoff prompt content
        handoff_prompt = parsed.get("gemini_handoff_prompt") if not resolved else None

        return HelpResponse(
            resolved=resolved,
            character_emotion_state=parsed.get("character_emotion_state"),
            gemini_handoff_prompt=handoff_prompt,
            turns_remaining=turns_remaining,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "Help turn failed",
            extra={"session_id": session_id, "error": str(exc)},
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Help turn failed") from exc


# ---------------------------------------------------------------------------
# POST /session/{session_id}/complete
# ---------------------------------------------------------------------------


@app.post("/session/{session_id}/complete")
async def complete_session(session_id: str) -> SessionCompleteResponse:
    """
    Run SummaryAgent to write session record + FSRS updates to Firestore.
    Returns the session summary JSON.
    """
    data = _get_session(session_id)

    # Build a summary prompt from session data
    import time as _time
    ctx = data.context_output
    concept_id = ctx.get("next_concept_id", "L01")
    total_q = data.quiz_questions_asked
    correct_q = data.quiz_correct
    score = round(correct_q / total_q, 2) if total_q > 0 else 0.0
    time_on_task = int(_time.time() - data.session_start_ts)
    help_triggered = data.phase == "help" or data.help_runner.turn_count > 0
    handoff_used = data.help_runner.turn_count >= 3
    prompt = (
        f"Summarise this session.\n"
        f"learner_uid: {data.uid}\n"
        f"lesson_id: {concept_id}\n"
        f"tier_used: {ctx.get('difficulty_tier', 'beginner')}\n"
        f"quiz_score_for_{concept_id}: {score}\n"
        f"time_on_task_seconds: {time_on_task}\n"
        f"help_triggered: {help_triggered}\n"
        f"gemini_handoff_used: {handoff_used}"
    )

    try:
        raw = await _run_agent_turn(
            _summary_runner, data.uid, data.summary_adk_session_id, prompt,
            agent_name="summary_agent",
        )
        parsed = _parse_json_response(raw, session_id, "summary_agent")

        data.phase = "complete"

        _log_summary_completion(session_id=session_id, parsed=parsed)

        # Clean up session from memory
        del _sessions[session_id]

        return SessionCompleteResponse(status="ok", summary=parsed)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "Session complete failed",
            extra={"session_id": session_id, "error": str(exc)},
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Session completion failed") from exc


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _log_summary_completion(*, session_id: str, parsed: dict[str, Any]) -> None:
    """Log summary fields. Privacy: never log gemini_handoff_prompt content."""
    safe = {k: v for k, v in parsed.items() if k != "gemini_handoff_prompt"}
    logger.info("Session completed", extra={"session_id": session_id, "summary": safe})
