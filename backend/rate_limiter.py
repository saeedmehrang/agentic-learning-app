"""
Per-UID session-start rate limiter backed by Firestore.

Limits each UID to MAX_SESSIONS_PER_HOUR session starts within a rolling
60-minute window. Uses a Firestore transaction to be safe under concurrent
requests from the same UID.

Firestore schema:
    rate_limits/{uid}
        count:        int        — starts in the current window
        window_start: timestamp  — UTC start of the current window
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from google.cloud import firestore

from config import settings

_WINDOW = timedelta(hours=1)


class RateLimitExceeded(Exception):
    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"Rate limit exceeded — retry after {retry_after_seconds}s")


def _update_in_transaction(
    transaction: firestore.Transaction,
    ref: firestore.DocumentReference,
    max_per_hour: int,
) -> None:
    doc = ref.get(transaction=transaction)
    now = datetime.now(UTC)

    if doc.exists:
        data = doc.to_dict() or {}
        window_start: datetime = data.get("window_start", now)
        if window_start.tzinfo is None:
            window_start = window_start.replace(tzinfo=UTC)
        count: int = data.get("count", 0)

        if now - window_start >= _WINDOW:
            transaction.set(ref, {"count": 1, "window_start": now})
        elif count >= max_per_hour:
            retry_after = int((_WINDOW - (now - window_start)).total_seconds())
            raise RateLimitExceeded(retry_after_seconds=max(retry_after, 1))
        else:
            transaction.update(ref, {"count": count + 1})
    else:
        transaction.set(ref, {"count": 1, "window_start": now})


def check_rate_limit(uid: str) -> None:
    """
    Enforce per-UID rate limit. Raises RateLimitExceeded if the UID has
    exceeded MAX_SESSIONS_PER_HOUR starts within the last 60 minutes.
    """
    max_per_hour: int = settings.max_sessions_per_hour
    db = firestore.Client(project=settings.gcp_project_id)
    ref = db.collection("rate_limits").document(uid)
    transaction = db.transaction()
    firestore.transactional(_update_in_transaction)(transaction, ref, max_per_hour)
