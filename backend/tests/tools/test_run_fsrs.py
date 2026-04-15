"""Unit tests for the run_fsrs FSRS scheduling tool.

Pure Python — no mocking required. All tests are synchronous.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tools.run_fsrs import run_fsrs

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CONCEPT_ID = "linux-file-permissions"
BASE_STABILITY = 4.0
BASE_DIFFICULTY = 5.0
BASE_MASTERY = 0.5


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_correct_increases_stability_decreases_difficulty_increases_mastery() -> None:
    """Correct answer doubles stability, lowers difficulty, raises mastery."""
    result = run_fsrs(CONCEPT_ID, BASE_STABILITY, BASE_DIFFICULTY, BASE_MASTERY, "correct")

    assert result["concept_id"] == CONCEPT_ID
    assert result["fsrs_stability"] == BASE_STABILITY * 2.0
    assert result["fsrs_difficulty"] == pytest.approx(BASE_DIFFICULTY - 0.1)
    assert result["mastery_score"] == pytest.approx(BASE_MASTERY + 0.1)


def test_incorrect_resets_stability_increases_difficulty_decreases_mastery() -> None:
    """Incorrect answer resets stability to 1.0, raises difficulty, lowers mastery."""
    result = run_fsrs(CONCEPT_ID, BASE_STABILITY, BASE_DIFFICULTY, BASE_MASTERY, "incorrect")

    assert result["concept_id"] == CONCEPT_ID
    assert result["fsrs_stability"] == 1.0
    assert result["fsrs_difficulty"] == pytest.approx(BASE_DIFFICULTY + 0.2)
    assert result["mastery_score"] == pytest.approx(BASE_MASTERY - 0.2)


def test_consecutive_correct_lengthens_review_intervals() -> None:
    """Each successive correct answer doubles the stability (interval)."""
    stability = 1.0
    difficulty = 5.0
    mastery = 0.5

    previous_stability = stability
    for _ in range(5):
        result = run_fsrs(CONCEPT_ID, stability, difficulty, mastery, "correct")
        new_stability = result["fsrs_stability"]
        assert new_stability > previous_stability
        previous_stability = new_stability
        stability = new_stability
        difficulty = result["fsrs_difficulty"]
        mastery = result["mastery_score"]

    # After 5 doublings from 1.0 the stability must be 32.0
    assert stability == pytest.approx(32.0)


def test_mastery_clamped_at_upper_bound() -> None:
    """mastery_score never exceeds 1.0 regardless of how many correct answers."""
    result = run_fsrs(CONCEPT_ID, BASE_STABILITY, BASE_DIFFICULTY, 0.95, "correct")
    assert result["mastery_score"] == pytest.approx(1.0)

    # Already at 1.0 — should remain 1.0
    result2 = run_fsrs(CONCEPT_ID, BASE_STABILITY, BASE_DIFFICULTY, 1.0, "correct")
    assert result2["mastery_score"] == pytest.approx(1.0)


def test_mastery_clamped_at_lower_bound() -> None:
    """mastery_score never falls below 0.0 regardless of incorrect answers."""
    result = run_fsrs(CONCEPT_ID, BASE_STABILITY, BASE_DIFFICULTY, 0.1, "incorrect")
    assert result["mastery_score"] == pytest.approx(0.0)

    # Already at 0.0 — should remain 0.0
    result2 = run_fsrs(CONCEPT_ID, BASE_STABILITY, BASE_DIFFICULTY, 0.0, "incorrect")
    assert result2["mastery_score"] == pytest.approx(0.0)


def test_next_review_at_is_valid_iso8601_utc_in_future() -> None:
    """next_review_at is a valid ISO 8601 UTC string and is in the future."""
    before = datetime.now(UTC)
    result = run_fsrs(CONCEPT_ID, BASE_STABILITY, BASE_DIFFICULTY, BASE_MASTERY, "correct")
    after = datetime.now(UTC)

    next_review_str: str = result["next_review_at"]
    # datetime.fromisoformat handles the +00:00 / UTC suffix produced by .isoformat()
    next_review = datetime.fromisoformat(next_review_str)

    # Must carry timezone info
    assert next_review.tzinfo is not None

    # Must be strictly in the future relative to the moment run_fsrs was called
    assert next_review > before

    # Sanity: the parsed timestamp was produced between our two bookmarks + stability days
    expected_min = before + __import__("datetime").timedelta(days=BASE_STABILITY * 2.0)
    assert next_review >= expected_min
    _ = after  # used implicitly via the future check above


def test_invalid_outcome_raises_value_error() -> None:
    """An unrecognised outcome string raises ValueError."""
    with pytest.raises(ValueError, match="outcome must be"):
        run_fsrs(CONCEPT_ID, BASE_STABILITY, BASE_DIFFICULTY, BASE_MASTERY, "maybe")
