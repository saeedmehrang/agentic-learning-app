"""
Load / concurrency integration tests for the Cloud Run backend.

Skipped automatically when CLOUD_RUN_URL is not set.
"""
from __future__ import annotations

import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest
import requests

CLOUD_RUN_URL = os.getenv("CLOUD_RUN_URL", "").rstrip("/")
pytestmark = pytest.mark.skipif(
    not CLOUD_RUN_URL,
    reason="CLOUD_RUN_URL not set — skipping integration tests",
)

_TIMEOUT = 60  # seconds per request


def _fresh_uid() -> str:
    return f"test-{int(time.time())}-{uuid.uuid4().hex[:6]}"


def _start_session(uid: str) -> tuple[int, dict]:  # type: ignore[type-arg]
    r = requests.post(
        f"{CLOUD_RUN_URL}/session/start",
        json={"uid": uid},
        timeout=_TIMEOUT,
    )
    return r.status_code, r.json() if r.status_code == 200 else {}


def _complete_session(session_id: str) -> None:
    try:
        requests.post(
            f"{CLOUD_RUN_URL}/session/{session_id}/complete",
            json={},
            timeout=_TIMEOUT,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Test 1 — 10 concurrent session starts
# ---------------------------------------------------------------------------


def test_concurrent_session_starts() -> None:
    num_sessions = 10
    uids = [_fresh_uid() for _ in range(num_sessions)]

    session_ids: list[str] = []
    start_wall = time.time()

    with ThreadPoolExecutor(max_workers=num_sessions) as executor:
        futures = {executor.submit(_start_session, uid): uid for uid in uids}
        for future in as_completed(futures):
            status_code, body = future.result()
            assert status_code == 200, f"Expected 200, got {status_code}"
            sid = body.get("session_id", "")
            assert sid, "session_id must be non-empty"
            session_ids.append(sid)

    elapsed = time.time() - start_wall
    assert elapsed < 30, f"10 concurrent starts took {elapsed:.1f}s — exceeds 30s budget"

    # All session_ids must be unique
    assert len(set(session_ids)) == num_sessions, "Duplicate session_ids detected"

    # Cleanup
    with ThreadPoolExecutor(max_workers=num_sessions) as executor:
        for sid in session_ids:
            executor.submit(_complete_session, sid)


# ---------------------------------------------------------------------------
# Test 2 — response time baseline for a single session start
# ---------------------------------------------------------------------------


def test_single_session_start_response_time() -> None:
    uid = _fresh_uid()
    session_id = ""
    start = time.time()
    r = requests.post(
        f"{CLOUD_RUN_URL}/session/start",
        json={"uid": uid},
        timeout=_TIMEOUT,
    )
    elapsed_ms = (time.time() - start) * 1000

    assert r.status_code == 200, r.text
    session_id = r.json()["session_id"]
    assert elapsed_ms < 5000, f"Session start took {elapsed_ms:.0f}ms — exceeds 5000ms budget"

    _complete_session(session_id)
