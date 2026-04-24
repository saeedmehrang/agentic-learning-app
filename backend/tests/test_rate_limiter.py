"""
Unit tests for rate_limiter.check_rate_limit().

All Firestore calls are mocked — no network, no real GCP.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from config import settings
from rate_limiter import RateLimitExceeded, _update_in_transaction, check_rate_limit

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


# ---------------------------------------------------------------------------
# Tests for check_rate_limit() — the public entry point
# ---------------------------------------------------------------------------


def test_check_rate_limit_wires_correctly() -> None:
    """check_rate_limit() uses correct collection path, uid, and max_per_hour."""
    mock_db = MagicMock()
    mock_ref = MagicMock()
    mock_txn = MagicMock()
    mock_db.collection.return_value.document.return_value = mock_ref
    mock_db.transaction.return_value = mock_txn

    with (
        patch("rate_limiter._get_db", return_value=mock_db),
        patch("rate_limiter.firestore.transactional") as mock_transactional,
    ):
        callable_mock = MagicMock()
        mock_transactional.return_value = callable_mock
        check_rate_limit("uid-abc")

    mock_db.collection.assert_called_once_with("rate_limits")
    mock_db.collection.return_value.document.assert_called_once_with("uid-abc")
    mock_transactional.assert_called_once_with(_update_in_transaction)
    callable_mock.assert_called_once_with(mock_txn, mock_ref, settings.max_sessions_per_hour)


def test_check_rate_limit_fails_open_on_firestore_error() -> None:
    """Firestore unavailability must NOT raise — limiter fails open."""
    with patch("rate_limiter._get_db", side_effect=Exception("Firestore down")):
        check_rate_limit("uid-xyz")  # must not raise


def test_get_db_singleton_reuses_client() -> None:
    """_get_db() returns the same client object on repeated calls."""
    import rate_limiter

    original = rate_limiter._db
    try:
        rate_limiter._db = None  # reset to force creation
        with patch("rate_limiter.firestore.Client") as mock_client_cls:
            mock_client_cls.return_value = MagicMock()
            first = rate_limiter._get_db()
            second = rate_limiter._get_db()
        assert first is second
        mock_client_cls.assert_called_once()  # constructed only once
    finally:
        rate_limiter._db = original  # restore so other tests are unaffected


def test_timezone_naive_window_start_treated_as_utc() -> None:
    """A timezone-naive window_start from Firestore must be treated as UTC,
    not cause a TypeError when compared against an aware datetime."""
    naive_window = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=30)
    ref = _make_ref({"count": 5, "window_start": naive_window})
    txn = _make_txn()
    # Must not raise TypeError — naive vs aware comparison
    _update_in_transaction(txn, ref, MAX)
    txn.update.assert_called_once()


def test_check_rate_limit_propagates_rate_limit_exceeded() -> None:
    """RateLimitExceeded must NOT be swallowed by the fail-open handler."""
    mock_db = MagicMock()
    mock_ref = MagicMock()
    mock_txn = MagicMock()
    mock_db.collection.return_value.document.return_value = mock_ref
    mock_db.transaction.return_value = mock_txn

    with (
        patch("rate_limiter._get_db", return_value=mock_db),
        patch("rate_limiter.firestore.transactional") as mock_transactional,
    ):
        mock_transactional.return_value = MagicMock(
            side_effect=RateLimitExceeded(retry_after_seconds=300)
        )
        with pytest.raises(RateLimitExceeded):
            check_rate_limit("uid-xyz")
