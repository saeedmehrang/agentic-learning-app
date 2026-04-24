"""
FastAPI entrypoint for the agentic learning backend.

Session lifecycle (per-turn interactive API)
--------------------------------------------
1. POST  /session/start            → Firestore read + scheduler picks next lesson.
                                     Creates LessonSession. Returns session_id + metadata.
2. GET   /session/{id}/lesson      → LessonSession.teach() (Turn 1).
3. GET   /session/{id}/quiz/question → LessonSession.next_question().
4. POST  /session/{id}/quiz/answer → LessonSession.evaluate_answer().
5. POST  /session/{id}/help        → HelpSession.respond() (max 3 turns, hard-capped).
6. POST  /session/{id}/complete    → summary_call.run_summary() + Firestore writes.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
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
from lesson_session import LessonSession
from logging_config import configure_logging
from handoff import get_handoff_provider
from rate_limiter import RateLimitExceeded, check_rate_limit

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
    # LessonSession instance for this session — set in session_start
    lesson_session: Any = None
    # Running score deltas per concept: concept_id → cumulative delta
    quiz_scores: dict[str, float] = field(default_factory=dict)
    quiz_questions_asked: int = 0
    quiz_correct: int = 0
    session_start_ts: float = field(default_factory=time.time)
    help_turn_count: int = 0
    phase: str = "lesson"  # lesson | quiz | help | complete


_sessions: dict[str, SessionData] = {}

# ---------------------------------------------------------------------------
# Course content stores — populated at startup by build_caches()
# Injected into LessonSession on every POST /session/start.
# ---------------------------------------------------------------------------

# lesson_store: "{lesson_id}:{tier}" → parsed lesson JSON dict (87 entries in prod)
_lesson_store: dict[str, dict[str, Any]] = {}
# Parsed outlines.yaml — list of lesson definition dicts
_outlines: Any = {}
# Parsed concept_map.json — dict with lessons, modules, cross_lesson_requirements
_concept_map: Any = {}

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _lesson_store, _outlines, _concept_map
    logger.info("Backend starting", extra={"app_env": settings.app_env})

    import cache_manager as _cache_manager

    _lesson_store, _outlines, _concept_map = _cache_manager.build_caches()
    logger.info(
        "Content loaded at startup",
        extra={
            "lesson_count": len(_lesson_store),
            "cache_enabled": _cache_manager.is_enabled(),
            "outlines_loaded": bool(_outlines),
            "concept_map_loaded": bool(_concept_map),
        },
    )

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
    handoff_url: str | None = None
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


def _read_learner_concepts(uid: str) -> list[dict[str, Any]]:
    """
    Read the learner's concept records from Firestore.

    Returns a flat list of concept dicts, each containing at minimum:
        lesson_id, mastery_score, next_review_at.

    For a brand-new learner with no Firestore documents this returns [].
    Firestore errors are logged and treated as an empty list (scheduler
    falls back to L01/beginner for new learners).
    """
    try:
        from google.cloud import firestore

        db = firestore.Client(project=settings.gcp_project_id)
        concepts_ref = (
            db.collection("learners")
            .document(uid)
            .collection("concepts")
        )
        docs = concepts_ref.stream()
        concepts: list[dict[str, Any]] = []
        for doc in docs:
            data = doc.to_dict() or {}
            # Each concepts document is keyed by lesson_id and contains per-concept
            # FSRS fields. Flatten: produce one entry per document with lesson_id set.
            data.setdefault("lesson_id", doc.id)
            concepts.append(data)
        return concepts
    except Exception as exc:
        logger.warning(
            "Firestore concepts read failed for uid=%s — treating as new learner: %s",
            uid, exc,
        )
        return []


# ---------------------------------------------------------------------------
# POST /session/start
# ---------------------------------------------------------------------------


@app.post("/session/start", response_model=SessionStartResponse)
async def session_start(request: SessionStartRequest) -> SessionStartResponse:
    """
    Start a new learning session.
    Reads Firestore learner concepts, picks next lesson via scheduler,
    creates a LessonSession, returns session_id + lesson metadata.
    """
    session_id = str(uuid.uuid4())
    with _tracer.start_as_current_span("session.start") as span:
        span.set_attribute("uid", request.uid)
        try:
            import cache_manager as _cache_manager
            import scheduler as _scheduler

            # 0. Rate limit — max N session starts per UID per rolling hour
            try:
                await asyncio.to_thread(check_rate_limit, request.uid)
            except RateLimitExceeded as exc:
                raise HTTPException(
                    status_code=429,
                    detail="Rate limit exceeded — too many session starts",
                    headers={"Retry-After": str(exc.retry_after_seconds)},
                )

            # 1. Firestore read — learner concepts
            concepts = _read_learner_concepts(request.uid)

            # 2. Scheduler picks next lesson
            picked = _scheduler.pick_next_lesson(concepts)
            lesson_id: str = picked["lesson_id"]
            tier: str = picked["tier"]
            character_id: str = picked["character_id"]

            # 3. Look up lesson content from in-memory store
            content_key = f"{lesson_id}:{tier}"
            lesson_content = _lesson_store.get(content_key)
            if lesson_content is None:
                # Fall back gracefully — use empty content rather than 500
                logger.warning(
                    "Lesson content not found for %s — using empty dict", content_key
                )
                lesson_content = {"lesson_id": lesson_id, "tier": tier, "lesson": {}, "quiz": {}}

            # 4. Get cache handle (None when caching disabled)
            cache_handle = _cache_manager.get_cache(lesson_id)

            # 5. Create LessonSession
            lesson_session = LessonSession(
                lesson_id=lesson_id,
                tier=tier,
                lesson_content=lesson_content,
                outlines=_outlines,
                concept_map=_concept_map,
                cached_content=cache_handle,
            )

            _sessions[session_id] = SessionData(
                session_id=session_id,
                uid=request.uid,
                lesson_id=lesson_id,
                tier=tier,
                character_id=character_id,
                lesson_session=lesson_session,
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
    """Deliver the lesson (LessonSession.teach(), Turn 1)."""
    data = _get_session(session_id)
    if data.phase != "lesson":
        raise HTTPException(
            status_code=409, detail=f"Session phase is '{data.phase}', expected 'lesson'"
        )
    with _tracer.start_as_current_span("session.lesson") as span:
        span.set_attribute("session_id", session_id)
        try:
            result = data.lesson_session.teach()
            data.phase = "quiz"
            return LessonResponse(
                lesson_text=result["lesson_text"],
                character_emotion_state=result["character_emotion_state"],
                key_concepts=result.get("key_concepts", []),
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
    """Request the next quiz question (LessonSession.next_question())."""
    data = _get_session(session_id)
    if data.phase not in ("quiz", "help"):
        raise HTTPException(
            status_code=409, detail=f"Session phase is '{data.phase}', expected 'quiz'"
        )
    with _tracer.start_as_current_span("session.quiz.question") as span:
        span.set_attribute("session_id", session_id)
        try:
            result = data.lesson_session.next_question()
            data.quiz_questions_asked += 1
            return QuizQuestionResponse(
                question_text=result["question_text"],
                format=result["format"],
                options=result.get("options", []),
                character_emotion_state=result["character_emotion_state"],
            )
        except IndexError:
            raise HTTPException(status_code=409, detail="No more quiz questions")
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
    """Submit a quiz answer (LessonSession.evaluate_answer())."""
    data = _get_session(session_id)
    if data.phase not in ("quiz", "help"):
        raise HTTPException(
            status_code=409, detail=f"Session phase is '{data.phase}', expected 'quiz'"
        )
    with _tracer.start_as_current_span("session.quiz.answer") as span:
        span.set_attribute("session_id", session_id)
        try:
            result = data.lesson_session.evaluate_answer(request.answer)

            # Track correct count
            if result["correct"]:
                data.quiz_correct += 1

            # Accumulate concept score deltas (keyed by current question index)
            concept_key = f"q{data.quiz_questions_asked}"
            delta = float(result.get("concept_score_delta", 0.0))
            data.quiz_scores[concept_key] = data.quiz_scores.get(concept_key, 0.0) + delta

            # If help triggered, set phase to help so subsequent help requests are accepted
            if result.get("trigger_help"):
                data.phase = "help"

            return QuizAnswerResponse(
                correct=result["correct"],
                explanation=result["explanation"],
                concept_score_delta=delta,
                character_emotion_state=result["character_emotion_state"],
                trigger_help=result.get("trigger_help", False),
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
            help_session = data.lesson_session.help_session
            if help_session is None:
                raise HTTPException(
                    status_code=409, detail="No active HelpSession for this quiz question"
                )

            result = help_session.respond(request.message)
            data.help_turn_count += 1

            turns_remaining = max(0, 3 - data.help_turn_count)

            # After cap or resolved: return to quiz phase
            if data.help_turn_count >= 3 or result.get("resolved"):
                data.phase = "quiz"

            handoff_prompt = result.get("gemini_handoff_prompt")
            handoff_url: str | None = None
            if handoff_prompt:
                handoff_url = get_handoff_provider().build_url(handoff_prompt, data.lesson_id)

            return HelpResponse(
                resolved=result.get("resolved", False),
                character_emotion_state=result.get("character_emotion_state"),
                gemini_handoff_prompt=handoff_prompt,
                handoff_url=handoff_url,
                turns_remaining=turns_remaining,
            )
        except HTTPException:
            raise
        except RuntimeError as exc:
            # HelpSession raises RuntimeError on 4th call — translate to 409
            raise HTTPException(status_code=409, detail=str(exc)) from exc
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
    Run summary_call.run_summary() to write session record + FSRS updates to Firestore.
    """
    data = _get_session(session_id)
    with _tracer.start_as_current_span("session.complete") as span:
        span.set_attribute("session_id", session_id)
        try:
            import summary_call as _summary_call

            time_on_task = (
                request.time_on_task_seconds
                if request.time_on_task_seconds != 0
                else int(time.time() - data.session_start_ts)
            )

            session_input: dict[str, Any] = {
                "uid": data.uid,
                "session_id": data.session_id,
                "lesson_id": data.lesson_id,
                "tier": data.tier,
                "quiz_scores": data.quiz_scores,
                "time_on_task_seconds": time_on_task,
                "help_triggered": data.help_turn_count > 0,
                "gemini_handoff_used": data.help_turn_count >= 3,
            }

            summary = _summary_call.run_summary(session_input)

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
