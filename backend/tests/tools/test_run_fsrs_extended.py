"""
Extended edge-case tests for run_fsrs.

Covers boundary conditions that the core test file doesn't reach:
- difficulty floor (1.0) and ceiling (10.0)
- stability floor (1.0 on first incorrect from any starting value)
- concept_id pass-through for multiple distinct concepts
- monotone-decreasing stability after repeated incorrect answers
- output keys are exhaustive — no extra, no missing
"""
from __future__ import annotations

import pytest

from tools.run_fsrs import run_fsrs

CONCEPT_ID = "linux-permissions"


# ---------------------------------------------------------------------------
# Difficulty boundary enforcement
# ---------------------------------------------------------------------------


class TestDifficultyBounds:
    def test_difficulty_never_falls_below_1_0_on_correct(self) -> None:
        """Difficulty floor is 1.0; subtracting from 1.1 must not go below 1.0."""
        result = run_fsrs(CONCEPT_ID, 2.0, 1.1, 0.5, "correct")
        assert result["fsrs_difficulty"] == pytest.approx(1.0)

    def test_difficulty_already_at_floor_stays_at_floor(self) -> None:
        result = run_fsrs(CONCEPT_ID, 2.0, 1.0, 0.5, "correct")
        assert result["fsrs_difficulty"] == pytest.approx(1.0)

    def test_difficulty_never_exceeds_10_0_on_incorrect(self) -> None:
        """Difficulty cap is 10.0; adding to 9.9 must not exceed 10.0."""
        result = run_fsrs(CONCEPT_ID, 2.0, 9.9, 0.5, "incorrect")
        assert result["fsrs_difficulty"] == pytest.approx(10.0)

    def test_difficulty_already_at_cap_stays_at_cap(self) -> None:
        result = run_fsrs(CONCEPT_ID, 2.0, 10.0, 0.5, "incorrect")
        assert result["fsrs_difficulty"] == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# Stability floor on incorrect
# ---------------------------------------------------------------------------


class TestStabilityFloor:
    def test_incorrect_always_resets_stability_to_1(self) -> None:
        """No matter how large stability is, an incorrect answer resets it to 1.0."""
        for large_stability in (1.0, 8.0, 64.0, 1024.0):
            result = run_fsrs(CONCEPT_ID, large_stability, 5.0, 0.5, "incorrect")
            assert result["fsrs_stability"] == pytest.approx(1.0), (
                f"Expected stability=1.0 after incorrect, got {result['fsrs_stability']} "
                f"(starting stability={large_stability})"
            )

    def test_stability_never_goes_below_1_on_incorrect(self) -> None:
        """Stability is 1.0 going in; incorrect must keep it at 1.0 not lower."""
        result = run_fsrs(CONCEPT_ID, 1.0, 5.0, 0.5, "incorrect")
        assert result["fsrs_stability"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Correct answer doubling
# ---------------------------------------------------------------------------


class TestStabilityDoubling:
    @pytest.mark.parametrize("start", [0.5, 1.0, 3.0, 7.5])
    def test_correct_doubles_stability(self, start: float) -> None:
        result = run_fsrs(CONCEPT_ID, start, 5.0, 0.5, "correct")
        assert result["fsrs_stability"] == pytest.approx(start * 2.0)


# ---------------------------------------------------------------------------
# concept_id pass-through
# ---------------------------------------------------------------------------


class TestConceptIdPassthrough:
    def test_concept_id_unchanged_on_correct(self) -> None:
        cid = "module-3-concept-7"
        result = run_fsrs(cid, 2.0, 5.0, 0.5, "correct")
        assert result["concept_id"] == cid

    def test_concept_id_unchanged_on_incorrect(self) -> None:
        cid = "module-3-concept-7"
        result = run_fsrs(cid, 2.0, 5.0, 0.5, "incorrect")
        assert result["concept_id"] == cid

    def test_multiple_distinct_concepts_independent(self) -> None:
        r1 = run_fsrs("concept-A", 2.0, 5.0, 0.5, "correct")
        r2 = run_fsrs("concept-B", 8.0, 3.0, 0.8, "correct")
        assert r1["concept_id"] == "concept-A"
        assert r2["concept_id"] == "concept-B"
        # Results are independent — each doubled its own stability
        assert r1["fsrs_stability"] == pytest.approx(4.0)
        assert r2["fsrs_stability"] == pytest.approx(16.0)


# ---------------------------------------------------------------------------
# Repeated incorrect answers keep stability pinned at 1.0
# ---------------------------------------------------------------------------


class TestRepeatedIncorrect:
    def test_stability_stays_at_one_through_multiple_incorrect(self) -> None:
        stability = 16.0
        difficulty = 5.0
        mastery = 0.6
        for _ in range(5):
            result = run_fsrs(CONCEPT_ID, stability, difficulty, mastery, "incorrect")
            stability = float(result["fsrs_stability"])  # type: ignore[arg-type]
            difficulty = float(result["fsrs_difficulty"])  # type: ignore[arg-type]
            mastery = float(result["mastery_score"])  # type: ignore[arg-type]
            assert stability == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Return dict completeness
# ---------------------------------------------------------------------------


class TestReturnShape:
    EXPECTED_KEYS = frozenset({
        "concept_id",
        "fsrs_stability",
        "fsrs_difficulty",
        "next_review_at",
        "mastery_score",
    })

    def test_correct_returns_exactly_expected_keys(self) -> None:
        result = run_fsrs(CONCEPT_ID, 2.0, 5.0, 0.5, "correct")
        assert set(result.keys()) == self.EXPECTED_KEYS

    def test_incorrect_returns_exactly_expected_keys(self) -> None:
        result = run_fsrs(CONCEPT_ID, 2.0, 5.0, 0.5, "incorrect")
        assert set(result.keys()) == self.EXPECTED_KEYS


# ---------------------------------------------------------------------------
# Invalid outcome
# ---------------------------------------------------------------------------


class TestInvalidOutcome:
    @pytest.mark.parametrize("bad", ["", "yes", "CORRECT", "True", "1"])
    def test_non_canonical_outcome_raises(self, bad: str) -> None:
        with pytest.raises(ValueError, match="outcome must be"):
            run_fsrs(CONCEPT_ID, 2.0, 5.0, 0.5, bad)
