"""
FastAPI entrypoint for the agentic learning backend.

Session lifecycle (per-turn interactive API)
--------------------------------------------
1. POST  /session/start            → Pure-Python scheduler picks next lesson.
                                     Returns session_id + {lesson_id, tier, character_id}.
2. GET   /session/{id}/lesson      → LessonSession teaching phase (Turn 1).
3. GET   /session/{id}/quiz/question → LessonSession quiz question.
4. POST  /session/{id}/quiz/answer → LessonSession answer evaluation.
5. POST  /session/{id}/help        → HelpSession (max 3 turns, hard-capped in Python).
6. POST  /session/{id}/complete    → SummaryCall + FSRS + Firestore writes.

All ADK / google-adk dependencies have been removed (PR-1).
LessonSession, HelpSession, SummaryCall are implemented in PR-3 and PR-4.
This file contains the HTTP skeleton wired to stub handlers until those PRs land.
"""
from __future__ import annotations

import logging
import os
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator

from fastapi import FastAPI, HTTPException
from opentelemetry import trace as otel_trace
from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.propagate import set_global_textmap
from opentelemetry.propagators.cloud_trace_propagator import CloudTraceFormatPropagator
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from pydantic import BaseModel

from config import settings
from logging_config import configure_logging

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", settings.gcp_project_id)
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", settings.gcp_location)

configure_logging()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# OpenTelemetry — Cloud Trace
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
# In-memory session store
# Keyed by session_id. Holds inter-turn state the HTTP layer needs.
# ---------------------------------------------------------------------------


@dataclass
class SessionData:
    session_id: str
    uid: str
    lesson_id: str
    tier: str
    character_id: str
    quiz_questions_asked: int = 0
    quiz_correct: int = 0
    session_start_ts: float = field(default_factory=lambda: __import__("time").time())
    help_turn_count: int = 0
    phase: str = "lesson"  # lesson | quiz | help | complete


_sessions: dict[str, SessionData] = {}

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("Backend starting", extra={"app_env": settings.app_env})
    # TODO PR-2: cache_manager.build_caches()
    # TODO PR-2: load approved lesson JSON files into in-memory store
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
    lesson_id: str
    tier: str
    character_id: str


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


class SessionCompleteRequest(BaseModel):
    time_on_task_seconds: int = 0


class SessionCompleteResponse(BaseModel):
    status: str
    summary: dict[str, Any]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_session(session_id: str) -> SessionData:
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
    Start a new learning session.
    Reads Firestore learner state, picks next lesson via pure-Python scheduler,
    returns session_id + lesson metadata.
    """
    session_id = str(uuid.uuid4())
    with _tracer.start_as_current_span("session.start") as span:
        span.set_attribute("uid", request.uid)
        try:
            # TODO PR-2: replace stubs with scheduler.pick_next_lesson(uid)
            lesson_id = "L01"
            tier = "beginner"
            character_id = "tux_jr"

            _sessions[session_id] = SessionData(
                session_id=session_id,
                uid=request.uid,
                lesson_id=lesson_id,
                tier=tier,
                character_id=character_id,
            )

            logger.info(
                "Session started",
                extra={
                    "session_id": session_id,
                    "uid": request.uid,
                    "lesson_id": lesson_id,
                    "tier": tier,
                },
            )
            return SessionStartResponse(
                status="ok",
                session_id=session_id,
                lesson_id=lesson_id,
                tier=tier,
                character_id=character_id,
            )
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(
                "Session start failed",
                extra={"uid": request.uid, "error": str(exc)},
                exc_info=True,
            )
            raise HTTPException(status_code=500, detail="Session initialisation failed") from exc


# ---------------------------------------------------------------------------
# GET /session/{session_id}/lesson
# ---------------------------------------------------------------------------


@app.get("/session/{session_id}/lesson")
async def get_lesson(session_id: str) -> LessonResponse:
    """
    Deliver the lesson for this session (LessonSession teaching phase, Turn 1).
    """
    data = _get_session(session_id)
    if data.phase != "lesson":
        raise HTTPException(
            status_code=409, detail=f"Session phase is '{data.phase}', expected 'lesson'"
        )
    with _tracer.start_as_current_span("session.lesson") as span:
        span.set_attribute("session_id", session_id)
        try:
            # TODO PR-3: replace stub with LessonSession.teach()
            data.phase = "quiz"
            return LessonResponse(
                lesson_text="[Lesson content — implement in PR-3]",
                character_emotion_state="teaching",
                key_concepts=[],
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
    """Request the next quiz question (LessonSession quiz phase)."""
    data = _get_session(session_id)
    if data.phase not in ("quiz", "help"):
        raise HTTPException(
            status_code=409, detail=f"Session phase is '{data.phase}', expected 'quiz'"
        )
    with _tracer.start_as_current_span("session.quiz.question") as span:
        span.set_attribute("session_id", session_id)
        try:
            # TODO PR-3: replace stub with LessonSession.next_question()
            data.quiz_questions_asked += 1
            return QuizQuestionResponse(
                question_text="[Quiz question — implement in PR-3]",
                format="mc",
                options=["A", "B", "C", "D"],
                character_emotion_state="curious",
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
    """Submit a quiz answer and get evaluation."""
    data = _get_session(session_id)
    if data.phase not in ("quiz", "help"):
        raise HTTPException(
            status_code=409, detail=f"Session phase is '{data.phase}', expected 'quiz'"
        )
    with _tracer.start_as_current_span("session.quiz.answer") as span:
        span.set_attribute("session_id", session_id)
        try:
            # TODO PR-3: replace stub with LessonSession.evaluate_answer(request.answer)
            return QuizAnswerResponse(
                correct=False,
                explanation="[Answer evaluation — implement in PR-3]",
                concept_score_delta=0.0,
                character_emotion_state="encouraging",
                trigger_help=False,
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
    Send a message to HelpSession. Hard-capped at 3 turns in Python.
    On turn 3 unresolved: returns gemini_handoff_prompt.
    """
    data = _get_session(session_id)
    if data.phase != "help":
        raise HTTPException(
            status_code=409, detail=f"Session phase is '{data.phase}', expected 'help'"
        )
    if data.help_turn_count >= 3:
        raise HTTPException(status_code=409, detail="HelpSession turn cap reached (3/3)")

    with _tracer.start_as_current_span("session.help") as span:
        span.set_attribute("session_id", session_id)
        span.set_attribute("help_turn", data.help_turn_count + 1)
        try:
            data.help_turn_count += 1
            # TODO PR-3: replace stub with HelpSession.respond(request.message)
            turns_remaining = max(0, 3 - data.help_turn_count)
            if data.help_turn_count >= 3:
                data.phase = "quiz"
            return HelpResponse(
                resolved=False,
                character_emotion_state="helping",
                gemini_handoff_prompt=None,
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
async def complete_session(
    session_id: str, request: SessionCompleteRequest
) -> SessionCompleteResponse:
    """
    Run SummaryCall to write session record + FSRS updates to Firestore.
    """
    data = _get_session(session_id)
    with _tracer.start_as_current_span("session.complete") as span:
        span.set_attribute("session_id", session_id)
        try:
            # TODO PR-4: replace stub with summary_call.run_summary(session_data)
            import time as _time
            summary: dict[str, Any] = {
                "lesson_id": data.lesson_id,
                "tier_used": data.tier,
                "quiz_questions_asked": data.quiz_questions_asked,
                "quiz_correct": data.quiz_correct,
                "time_on_task_seconds": (
                    request.time_on_task_seconds or int(_time.time() - data.session_start_ts)
                ),
                "help_triggered": data.help_turn_count > 0,
                "gemini_handoff_used": data.help_turn_count >= 3,
                "summary_text": "[Summary — implement in PR-4]",
            }

            logger.info(
                "Session completed",
                extra={
                    "session_id": session_id,
                    "lesson_id": data.lesson_id,
                    "quiz_correct": data.quiz_correct,
                    "quiz_total": data.quiz_questions_asked,
                },
            )

            del _sessions[session_id]
            return SessionCompleteResponse(status="ok", summary=summary)
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(
                "Session complete failed",
                extra={"session_id": session_id, "error": str(exc)},
                exc_info=True,
            )
            raise HTTPException(status_code=500, detail="Session completion failed") from exc
