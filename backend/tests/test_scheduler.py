"""
Unit tests for scheduler.pick_next_lesson().

No I/O, no LLM calls — pure Python.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from scheduler import _LESSON_MODULE, MODULE_CHARACTER, pick_next_lesson

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(tz=timezone.utc)


def _concept(
    lesson_id: str,
    mastery: float = 0.5,
    next_review_at: datetime | None = None,
) -> dict:
    return {
        "lesson_id": lesson_id,
        "mastery_score": mastery,
        "next_review_at": next_review_at.isoformat() if next_review_at else None,
    }


def _overdue(lesson_id: str, mastery: float = 0.5, hours_ago: float = 1.0) -> dict:
    return _concept(lesson_id, mastery, _NOW - timedelta(hours=hours_ago))


def _future(lesson_id: str, mastery: float = 0.5, hours_ahead: float = 24.0) -> dict:
    return _concept(lesson_id, mastery, _NOW + timedelta(hours=hours_ahead))


# ---------------------------------------------------------------------------
# New learner
# ---------------------------------------------------------------------------


class TestNewLearner:
    def test_empty_concepts_returns_l01_beginner(self) -> None:
        result = pick_next_lesson([])
        assert result["lesson_id"] == "L01"
        assert result["tier"] == "beginner"
        assert result["character_id"] == MODULE_CHARACTER[1]

    def test_returns_all_required_keys(self) -> None:
        result = pick_next_lesson([])
        assert set(result.keys()) == {"lesson_id", "tier", "character_id"}


# ---------------------------------------------------------------------------
# Overdue selection
# ---------------------------------------------------------------------------


class TestOverdueSelection:
    def test_single_overdue_concept_selected(self) -> None:
        concepts = [_overdue("L03"), _future("L01"), _future("L02")]
        result = pick_next_lesson(concepts)
        assert result["lesson_id"] == "L03"

    def test_most_overdue_selected_when_multiple_overdue(self) -> None:
        """Earliest next_review_at wins."""
        concepts = [
            _overdue("L02", hours_ago=1),
            _overdue("L05", hours_ago=48),  # most overdue
            _overdue("L03", hours_ago=12),
        ]
        result = pick_next_lesson(concepts)
        assert result["lesson_id"] == "L05"

    def test_future_concepts_ignored_when_overdue_present(self) -> None:
        concepts = [_future("L01", mastery=0.0), _overdue("L10")]
        result = pick_next_lesson(concepts)
        assert result["lesson_id"] == "L10"


# ---------------------------------------------------------------------------
# Mastery fallback (all future)
# ---------------------------------------------------------------------------


class TestMasteryFallback:
    def test_all_future_picks_lowest_mastery(self) -> None:
        concepts = [
            _future("L01", mastery=0.8),
            _future("L02", mastery=0.2),  # lowest
            _future("L03", mastery=0.6),
        ]
        result = pick_next_lesson(concepts)
        assert result["lesson_id"] == "L02"

    def test_all_none_review_at_picks_lowest_mastery(self) -> None:
        """next_review_at=None counts as not-overdue."""
        concepts = [
            _concept("L01", mastery=0.9),
            _concept("L02", mastery=0.1),  # lowest
            _concept("L03", mastery=0.5),
        ]
        result = pick_next_lesson(concepts)
        assert result["lesson_id"] == "L02"


# ---------------------------------------------------------------------------
# Tier assignment
# ---------------------------------------------------------------------------


class TestTierAssignment:
    @pytest.mark.parametrize(
        "mastery, expected_tier",
        [
            (0.0, "beginner"),
            (0.39, "beginner"),
            (0.4, "intermediate"),
            (0.74, "intermediate"),
            (0.75, "advanced"),
            (1.0, "advanced"),
        ],
    )
    def test_tier_thresholds(self, mastery: float, expected_tier: str) -> None:
        concepts = [_overdue("L01", mastery=mastery)]
        result = pick_next_lesson(concepts)
        assert result["tier"] == expected_tier, (
            f"mastery={mastery} → expected {expected_tier}, got {result['tier']}"
        )


# ---------------------------------------------------------------------------
# Character mapping
# ---------------------------------------------------------------------------


class TestCharacterMapping:
    def test_character_matches_module(self) -> None:
        """Each lesson returns the character for its module."""
        for lesson_id, module_id in _LESSON_MODULE.items():
            concepts = [_overdue(lesson_id)]
            result = pick_next_lesson(concepts)
            expected = MODULE_CHARACTER[module_id]
            got = result["character_id"]
            assert got == expected, (
                f"{lesson_id} (module {module_id}): expected {expected}, got {got}"
            )

    def test_all_9_modules_have_character_mapping(self) -> None:
        assert set(MODULE_CHARACTER.keys()) == set(range(1, 10))

    def test_all_29_lessons_have_module_mapping(self) -> None:
        assert len(_LESSON_MODULE) == 29
        expected_lessons = {f"L{i:02d}" for i in range(1, 30)}
        assert set(_LESSON_MODULE.keys()) == expected_lessons


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_unparseable_next_review_at_treated_as_not_overdue(self) -> None:
        concepts = [
            {"lesson_id": "L01", "mastery_score": 0.9, "next_review_at": "not-a-date"},
            _concept("L02", mastery=0.1),  # fallback: lowest mastery
        ]
        result = pick_next_lesson(concepts)
        assert result["lesson_id"] == "L02"

    def test_datetime_object_next_review_at_accepted(self) -> None:
        """next_review_at as a datetime object (as Firestore may return) is handled."""
        concepts = [
            {
                "lesson_id": "L05",
                "mastery_score": 0.5,
                "next_review_at": _NOW - timedelta(hours=2),  # datetime, not str
            }
        ]
        result = pick_next_lesson(concepts)
        assert result["lesson_id"] == "L05"

    def test_single_concept_always_selected(self) -> None:
        concepts = [_future("L07", mastery=0.99)]
        result = pick_next_lesson(concepts)
        assert result["lesson_id"] == "L07"
