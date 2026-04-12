"""
Edge-case and robustness tests for scheduler.pick_next_lesson().

Covers scenarios that can cause silent failures in production:
- Malformed Firestore documents (missing fields, wrong types)
- Timezone-naive datetimes (Firestore SDK returns these in some configs)
- Tie-breaking determinism
- All concepts missing lesson_id (total fallback)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from scheduler import pick_next_lesson

_NOW = datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# Missing / malformed fields from Firestore
# ---------------------------------------------------------------------------


class TestMalformedConcepts:
    def test_concept_missing_lesson_id_is_skipped(self) -> None:
        """A concept without lesson_id must not cause KeyError."""
        concepts = [
            {"mastery_score": 0.1, "next_review_at": None},  # no lesson_id
            {"lesson_id": "L05", "mastery_score": 0.9, "next_review_at": None},
        ]
        result = pick_next_lesson(concepts)
        assert result["lesson_id"] == "L05"

    def test_all_concepts_missing_lesson_id_falls_back_to_l01(self) -> None:
        """If every concept lacks lesson_id, fall back to L01 beginner."""
        concepts = [
            {"mastery_score": 0.1, "next_review_at": None},
            {"mastery_score": 0.2, "next_review_at": None},
        ]
        result = pick_next_lesson(concepts)
        assert result["lesson_id"] == "L01"
        assert result["tier"] == "beginner"

    def test_concept_with_empty_string_lesson_id_is_skipped(self) -> None:
        concepts = [
            {"lesson_id": "", "mastery_score": 0.0, "next_review_at": None},
            {"lesson_id": "L03", "mastery_score": 0.5, "next_review_at": None},
        ]
        result = pick_next_lesson(concepts)
        assert result["lesson_id"] == "L03"

    def test_concept_with_none_lesson_id_is_skipped(self) -> None:
        concepts = [
            {"lesson_id": None, "mastery_score": 0.0, "next_review_at": None},
            {"lesson_id": "L07", "mastery_score": 0.8, "next_review_at": None},
        ]
        result = pick_next_lesson(concepts)
        assert result["lesson_id"] == "L07"

    def test_missing_mastery_score_defaults_to_zero(self) -> None:
        """A concept without mastery_score should be treated as 0.0 (lowest)."""
        concepts = [
            {"lesson_id": "L01", "next_review_at": None},          # no mastery_score
            {"lesson_id": "L02", "mastery_score": 0.5, "next_review_at": None},
        ]
        result = pick_next_lesson(concepts)
        assert result["lesson_id"] == "L01"

    def test_non_numeric_mastery_score_does_not_crash(self) -> None:
        """Firestore type mismatch — mastery_score as string."""
        concepts = [
            {"lesson_id": "L01", "mastery_score": "bad", "next_review_at": None},
            {"lesson_id": "L02", "mastery_score": 0.5, "next_review_at": None},
        ]
        # Should not raise; either picks L02 or handles gracefully
        try:
            result = pick_next_lesson(concepts)
            assert "lesson_id" in result
        except (ValueError, TypeError):
            pytest.fail("pick_next_lesson crashed on non-numeric mastery_score")


# ---------------------------------------------------------------------------
# Timezone-naive datetimes (Firestore SDK behaviour in some configs)
# ---------------------------------------------------------------------------


class TestTimezoneNaiveDatetimes:
    def test_timezone_naive_overdue_datetime_treated_as_utc(self) -> None:
        """A naive datetime in the past should be treated as UTC and count as overdue."""
        naive_past = datetime.utcnow() - timedelta(hours=5)  # no tzinfo
        concepts = [
            {"lesson_id": "L04", "mastery_score": 0.5, "next_review_at": naive_past},
            {"lesson_id": "L05", "mastery_score": 0.5,
             "next_review_at": (_NOW + timedelta(days=1)).isoformat()},
        ]
        result = pick_next_lesson(concepts)
        assert result["lesson_id"] == "L04"

    def test_timezone_naive_future_datetime_not_overdue(self) -> None:
        """A naive datetime far in the future must not count as overdue."""
        naive_future = datetime.utcnow() + timedelta(days=7)
        concepts = [
            {"lesson_id": "L06", "mastery_score": 0.3, "next_review_at": naive_future},
            {"lesson_id": "L07", "mastery_score": 0.1, "next_review_at": None},
        ]
        # No overdue → mastery fallback → L07 (lowest mastery)
        result = pick_next_lesson(concepts)
        assert result["lesson_id"] == "L07"


# ---------------------------------------------------------------------------
# Tie-breaking
# ---------------------------------------------------------------------------


class TestTieBreaking:
    def test_identical_overdue_timestamps_returns_one_of_them(self) -> None:
        """Two concepts overdue at the exact same time — must return one deterministically."""
        ts = (_NOW - timedelta(hours=2)).isoformat()
        concepts = [
            {"lesson_id": "L01", "mastery_score": 0.5, "next_review_at": ts},
            {"lesson_id": "L02", "mastery_score": 0.5, "next_review_at": ts},
        ]
        results = {pick_next_lesson(concepts)["lesson_id"] for _ in range(10)}
        # Must always return the same one (stable min), not alternate
        assert len(results) == 1

    def test_identical_mastery_scores_returns_one_deterministically(self) -> None:
        """Two concepts with identical mastery — must always return the same one."""
        concepts = [
            {"lesson_id": "L10", "mastery_score": 0.3, "next_review_at": None},
            {"lesson_id": "L11", "mastery_score": 0.3, "next_review_at": None},
        ]
        results = {pick_next_lesson(concepts)["lesson_id"] for _ in range(10)}
        assert len(results) == 1

    def test_overdue_always_beats_lower_mastery_future(self) -> None:
        """An overdue concept with mastery=1.0 beats a future concept with mastery=0.0."""
        concepts = [
            {"lesson_id": "L01", "mastery_score": 1.0,
             "next_review_at": (_NOW - timedelta(hours=1)).isoformat()},
            {"lesson_id": "L02", "mastery_score": 0.0,
             "next_review_at": (_NOW + timedelta(days=1)).isoformat()},
        ]
        result = pick_next_lesson(concepts)
        assert result["lesson_id"] == "L01"


# ---------------------------------------------------------------------------
# Mixed valid and invalid concepts
# ---------------------------------------------------------------------------


class TestMixedValidInvalid:
    def test_one_invalid_one_valid_overdue(self) -> None:
        """Invalid concept is skipped; valid overdue concept is picked."""
        concepts = [
            {"mastery_score": 0.0, "next_review_at": None},  # missing lesson_id
            {"lesson_id": "L08", "mastery_score": 0.5,
             "next_review_at": (_NOW - timedelta(hours=1)).isoformat()},
        ]
        result = pick_next_lesson(concepts)
        assert result["lesson_id"] == "L08"

    def test_overdue_with_unparseable_ts_skipped_uses_mastery_fallback(self) -> None:
        """Unparseable timestamp → concept treated as not-overdue → mastery fallback."""
        concepts = [
            {"lesson_id": "L01", "mastery_score": 0.8, "next_review_at": "garbage-ts"},
            {"lesson_id": "L02", "mastery_score": 0.2, "next_review_at": None},
        ]
        result = pick_next_lesson(concepts)
        assert result["lesson_id"] == "L02"
