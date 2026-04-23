"""
Unit tests for rate_limiter.check_rate_limit().

All Firestore calls are mocked — no network, no real GCP.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from rate_limiter import RateLimitExceeded, _update_in_transaction

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ref(doc_data: dict | None) -> MagicMock:
    """Return a mock DocumentReference whose .get() returns doc_data."""
    doc_snap = MagicMock()
    doc_snap.exists = doc_data is not None
    doc_snap.to_dict.return_value = doc_data or {}

    ref = MagicMock()
    ref.get.return_value = doc_snap
    return ref


def _make_txn() -> MagicMock:
    return MagicMock()


MAX = 10  # mirrors settings default


# ---------------------------------------------------------------------------
# Tests against _update_in_transaction directly (pure logic, no SDK machinery)
# ---------------------------------------------------------------------------


def test_first_request_allowed() -> None:
    """Brand-new UID (no doc) → sets count=1, does not raise."""
    ref = _make_ref(None)
    txn = _make_txn()
    _update_in_transaction(txn, ref, MAX)  # must not raise
    txn.set.assert_called_once()


def test_within_limit_allowed() -> None:
    """9 prior starts in window → increments count, does not raise."""
    ref = _make_ref({"count": 9, "window_start": datetime.now(UTC)})
    txn = _make_txn()
    _update_in_transaction(txn, ref, MAX)  # must not raise
    txn.update.assert_called_once()


def test_limit_exceeded() -> None:
    """10 prior starts in window → raises RateLimitExceeded."""
    ref = _make_ref({"count": 10, "window_start": datetime.now(UTC)})
    txn = _make_txn()
    with pytest.raises(RateLimitExceeded) as exc_info:
        _update_in_transaction(txn, ref, MAX)
    assert exc_info.value.retry_after_seconds >= 1


def test_window_reset() -> None:
    """window_start > 60 min ago → resets counter, does not raise."""
    old_window = datetime.now(UTC) - timedelta(hours=2)
    ref = _make_ref({"count": 10, "window_start": old_window})
    txn = _make_txn()
    _update_in_transaction(txn, ref, MAX)  # must not raise
    txn.set.assert_called_once()


def test_retry_after_is_bounded() -> None:
    """Retry-After is between 1s and 3600s."""
    ref = _make_ref({"count": 10, "window_start": datetime.now(UTC)})
    txn = _make_txn()
    with pytest.raises(RateLimitExceeded) as exc_info:
        _update_in_transaction(txn, ref, MAX)
    assert 1 <= exc_info.value.retry_after_seconds <= 3600
