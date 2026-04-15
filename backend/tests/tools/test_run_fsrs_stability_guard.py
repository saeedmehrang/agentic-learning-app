"""
Guard tests for run_fsrs stability input validation.

Zero or negative stability would produce a next_review_at in the past,
silently scheduling an immediate re-review. The implementation now raises
ValueError for these inputs.
"""
from __future__ import annotations

import pytest

from tools.run_fsrs import run_fsrs

CID = "linux-permissions"


class TestStabilityInputGuard:
    @pytest.mark.parametrize("bad_stability", [0.0, -1.0, -100.0])
    def test_zero_or_negative_stability_raises(self, bad_stability: float) -> None:
        with pytest.raises(ValueError, match="fsrs_stability must be positive"):
            run_fsrs(CID, bad_stability, 5.0, 0.5, "correct")

    @pytest.mark.parametrize("bad_stability", [0.0, -0.001])
    def test_raises_for_incorrect_outcome_too(self, bad_stability: float) -> None:
        with pytest.raises(ValueError, match="fsrs_stability must be positive"):
            run_fsrs(CID, bad_stability, 5.0, 0.5, "incorrect")

    def test_small_positive_stability_does_not_raise(self) -> None:
        """0.001 is valid — next_review_at will be ~1.4 minutes in the future."""
        result = run_fsrs(CID, 0.001, 5.0, 0.5, "correct")
        assert result["fsrs_stability"] == pytest.approx(0.002)

    def test_next_review_always_in_future_for_valid_stability(self) -> None:
        from datetime import UTC, datetime
        result = run_fsrs(CID, 1.0, 5.0, 0.5, "correct")
        next_review = datetime.fromisoformat(result["next_review_at"])
        assert next_review > datetime.now(UTC)
