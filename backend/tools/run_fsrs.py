"""
run_fsrs — deterministic FSRS (Free Spaced Repetition Scheduler) tool.

Pure Python: no LLM, no I/O, no async. Called by SummaryAgent after each
lesson session to update a concept's spaced-repetition schedule.

Algorithm (simplified FSRS-4):
  correct  → stability doubles, difficulty decreases 0.1 (floor 1.0),
              mastery +0.1 (cap 1.0)
  incorrect → stability resets to 1.0, difficulty increases 0.2 (cap 10.0),
              mastery -0.2 (floor 0.0)

next_review_at = now(UTC) + timedelta(days=stability)
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta


def run_fsrs(
    concept_id: str,
    fsrs_stability: float,
    fsrs_difficulty: float,
    mastery_score: float,
    outcome: str,  # "correct" | "incorrect"
) -> dict[str, object]:
    """
    Update FSRS scheduling parameters for a single concept after a review.

    Args:
        concept_id: Unique identifier for the concept being reviewed.
        fsrs_stability: Current stability value (days until next review).
        fsrs_difficulty: Current difficulty value in [1.0, 10.0].
        mastery_score: Current mastery in [0.0, 1.0].
        outcome: Result of the review — "correct" or "incorrect".

    Returns:
        Dict with keys:
            concept_id (str): Unchanged concept identifier.
            fsrs_stability (float): Updated stability.
            fsrs_difficulty (float): Updated difficulty.
            next_review_at (str): ISO 8601 UTC timestamp for next review.
            mastery_score (float): Updated mastery score, clamped to [0.0, 1.0].

    Raises:
        ValueError: If outcome is not "correct" or "incorrect".
    """
    if outcome not in ("correct", "incorrect"):
        raise ValueError(f"outcome must be 'correct' or 'incorrect', got {outcome!r}")

    if outcome == "correct":
        new_stability: float = fsrs_stability * 2.0
        new_difficulty: float = max(1.0, fsrs_difficulty - 0.1)
        new_mastery: float = min(1.0, mastery_score + 0.1)
    else:  # incorrect
        new_stability = 1.0
        new_difficulty = min(10.0, fsrs_difficulty + 0.2)
        new_mastery = max(0.0, mastery_score - 0.2)

    next_review_at: str = (
        datetime.now(UTC) + timedelta(days=new_stability)
    ).isoformat()

    return {
        "concept_id": concept_id,
        "fsrs_stability": new_stability,
        "fsrs_difficulty": new_difficulty,
        "next_review_at": next_review_at,
        "mastery_score": new_mastery,
    }
