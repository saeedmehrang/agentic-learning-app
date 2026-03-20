"""
FastAPI entrypoint for the ADK learning backend.

Phase 3: health check + session start stub.
Phase 4: wire the ADK Runner to execute the full pipeline.
"""
from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from pydantic import BaseModel

from config import settings
from logging_config import configure_logging

configure_logging()
logger = logging.getLogger(__name__)


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


class SessionStartRequest(BaseModel):
    uid: str


class SessionStartResponse(BaseModel):
    status: str
    session_id: str


@app.post("/session/start", response_model=SessionStartResponse)
async def session_start(request: SessionStartRequest) -> SessionStartResponse:
    """
    Start a new learning session for the given learner UID.

    Phase 3 stub: logs the request and returns a placeholder session_id.
    Phase 4: instantiate ADK Runner, create session, execute pipeline.
    """
    session_id = str(uuid.uuid4())
    logger.info("Session start requested", extra={"uid": request.uid, "session_id": session_id})
    # TODO Phase 4: runner = Runner(agent=pipeline, ...); await runner.run_async(...)
    return SessionStartResponse(status="ok", session_id=session_id)
