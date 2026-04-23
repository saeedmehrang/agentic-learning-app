"""
Load / concurrency integration tests for the Cloud Run backend.

Skipped automatically when CLOUD_RUN_URL is not set.

Run with:
    CLOUD_RUN_URL=https://... python -m pytest tests/integration/test_load.py -v
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid

import httpx
import pytest

CLOUD_RUN_URL = os.getenv("CLOUD_RUN_URL", "").rstrip("/")
pytestmark = pytest.mark.skipif(
    not CLOUD_RUN_URL,
    reason="CLOUD_RUN_URL not set — skipping integration tests",
)

_TIMEOUT = 60.0  # seconds per request


def _load_uid() -> str:
    return f"load-test-{int(time.time())}-{uuid.uuid4().hex[:6]}"


# ---------------------------------------------------------------------------
# Test 1 — 10 concurrent session starts
# ---------------------------------------------------------------------------


def test_concurrent_session_starts() -> None:
    """10 POST /session/start requests in parallel — all must succeed within 15s."""

    async def _run() -> tuple[list[httpx.Response], float]:
        uids = [_load_uid() for _ in range(10)]
        start = time.monotonic()

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:

            async def start_session(uid: str) -> httpx.Response:
                return await client.post(
                    f"{CLOUD_RUN_URL}/session/start",
                    json={"uid": uid},
                )

            responses = await asyncio.gather(*[start_session(uid) for uid in uids])

        elapsed = time.monotonic() - start

        # Best-effort cleanup
        session_ids = [
            r.json()["session_id"] for r in responses if r.status_code == 200
        ]
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            await asyncio.gather(
                *[
                    client.post(f"{CLOUD_RUN_URL}/session/{sid}/complete", json={})
                    for sid in session_ids
                ],
                return_exceptions=True,
            )

        return list(responses), elapsed

    responses, elapsed = asyncio.run(_run())

    assert elapsed < 30, f"Concurrent starts took {elapsed:.1f}s — expected < 30s"
    assert all(r.status_code == 200 for r in responses), (
        f"Not all 200: {[r.status_code for r in responses]}"
    )
    session_ids = [r.json()["session_id"] for r in responses]
    assert len(set(session_ids)) == 10, "Duplicate session_ids returned"


# ---------------------------------------------------------------------------
# Test 2 — response time baseline (warm instance, runs after test 1)
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_response_time_baseline() -> None:
    """Single POST /session/start on a warm instance must respond within 3000ms."""

    async def _run() -> tuple[int, float, str]:
        uid = _load_uid()
        start = time.monotonic()
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(
                f"{CLOUD_RUN_URL}/session/start",
                json={"uid": uid},
            )
        elapsed_ms = (time.monotonic() - start) * 1000
        session_id = r.json().get("session_id", "") if r.status_code == 200 else ""

        if session_id:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                await client.post(
                    f"{CLOUD_RUN_URL}/session/{session_id}/complete",
                    json={},
                )

        return r.status_code, elapsed_ms, r.text

    status_code, elapsed_ms, body = asyncio.run(_run())

    assert status_code == 200, f"Expected 200, got {status_code}: {body}"
    assert elapsed_ms < 3000, f"Response took {elapsed_ms:.0f}ms — expected < 3000ms"
