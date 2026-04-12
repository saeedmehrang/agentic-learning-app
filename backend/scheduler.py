"""
Pure-Python lesson scheduler — no I/O, no LLM.

pick_next_lesson(concepts) selects the next lesson for a learner based on
their FSRS concept records from Firestore.

Selection logic (deterministic):
1. Find all concepts with next_review_at <= now (overdue) — pick the earliest.
2. If none are overdue, pick the concept with the lowest mastery_score (weakest knowledge).
3. If no concepts exist at all (new learner), start at L01, tier=beginner.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module → character mapping
# Replaces MODULE_CHARACTER from the deleted context_agent.py.
# Key: module_id (int), value: character_id (str)
# ---------------------------------------------------------------------------

MODULE_CHARACTER: dict[int, str] = {
    1: "tux_jr",       # Module 1 — Linux History & Distributions
    2: "cursor",       # Module 2 — The Shell
    3: "filo",         # Module 3 — Files & Directories
    4: "permsy",       # Module 4 — Permissions
    5: "pipey",        # Module 5 — Pipes & Redirection
    6: "procsy",       # Module 6 — Processes
    7: "nettie",       # Module 7 — Networking
    8: "scrippy",      # Module 8 — Shell Scripting
    9: "devy",         # Module 9 — Development Tools
}

# ---------------------------------------------------------------------------
# Lesson → module mapping (derived from outlines.yaml, kept in sync here)
# ---------------------------------------------------------------------------

_LESSON_MODULE: dict[str, int] = {
    "L01": 1, "L02": 1, "L03": 1,
    "L04": 2, "L05": 2, "L06": 2, "L07": 2,
    "L08": 3, "L09": 3, "L10": 3, "L11": 3,
    "L12": 4, "L13": 4, "L14": 4,
    "L15": 5, "L16": 5, "L17": 5,
    "L18": 6, "L19": 6, "L20": 6,
    "L21": 7, "L22": 7, "L23": 7,
    "L24": 8, "L25": 8,
    "L26": 9, "L27": 9, "L28": 9, "L29": 9,
}

# Tier thresholds — mastery_score → tier label
# mastery < 0.4  → beginner
# mastery < 0.75 → intermediate
# mastery >= 0.75 → advanced
_TIER_THRESHOLDS: list[tuple[float, str]] = [
    (0.4, "beginner"),
    (0.75, "intermediate"),
    (1.01, "advanced"),
]


def _tier_for_mastery(mastery_score: float) -> str:
    for threshold, tier in _TIER_THRESHOLDS:
        if mastery_score < threshold:
            return tier
    return "advanced"


def _character_for_lesson(lesson_id: str) -> str:
    module_id = _LESSON_MODULE.get(lesson_id, 1)
    return MODULE_CHARACTER.get(module_id, "tux_jr")


def pick_next_lesson(concepts: list[dict]) -> dict:
    """
    Select the next lesson for a learner.

    Args:
        concepts: list of concept dicts from Firestore
                  (learners/{uid}/concepts sub-collection).
                  Each dict must have at minimum:
                    - lesson_id: str          e.g. "L01"
                    - mastery_score: float    0.0–1.0
                    - next_review_at: str     ISO 8601 UTC timestamp, or None

    Returns:
        {
          "lesson_id": str,
          "tier": str,            # "beginner" | "intermediate" | "advanced"
          "character_id": str,    # module character
        }
    """
    if not concepts:
        logger.info("New learner — starting at L01 beginner")
        return {
            "lesson_id": "L01",
            "tier": "beginner",
            "character_id": MODULE_CHARACTER[1],
        }

    now = datetime.now(tz=timezone.utc)

    # --- Pass 1: overdue concepts (next_review_at in the past) ---------------
    overdue: list[dict] = []
    for c in concepts:
        raw_ts = c.get("next_review_at")
        if raw_ts is None:
            continue
        try:
            if isinstance(raw_ts, str):
                review_at = datetime.fromisoformat(raw_ts)
            elif isinstance(raw_ts, datetime):
                review_at = raw_ts
            else:
                continue
            # Ensure timezone-aware for comparison
            if review_at.tzinfo is None:
                review_at = review_at.replace(tzinfo=timezone.utc)
            if review_at <= now:
                overdue.append({**c, "_review_at": review_at})
        except (ValueError, TypeError):
            logger.warning("Unparseable next_review_at for concept %s", c.get("lesson_id"))

    if overdue:
        # Pick the most overdue (earliest next_review_at)
        chosen = min(overdue, key=lambda x: x["_review_at"])
        lesson_id = chosen["lesson_id"]
        mastery = float(chosen.get("mastery_score", 0.0))
        tier = _tier_for_mastery(mastery)
        logger.info(
            "Scheduler: overdue concept selected",
            extra={"lesson_id": lesson_id, "tier": tier, "mastery": mastery},
        )
        return {
            "lesson_id": lesson_id,
            "tier": tier,
            "character_id": _character_for_lesson(lesson_id),
        }

    # --- Pass 2: all future — pick lowest mastery_score ----------------------
    chosen = min(concepts, key=lambda c: float(c.get("mastery_score", 0.0)))
    lesson_id = chosen["lesson_id"]
    mastery = float(chosen.get("mastery_score", 0.0))
    tier = _tier_for_mastery(mastery)
    logger.info(
        "Scheduler: no overdue concepts — lowest mastery selected",
        extra={"lesson_id": lesson_id, "tier": tier, "mastery": mastery},
    )
    return {
        "lesson_id": lesson_id,
        "tier": tier,
        "character_id": _character_for_lesson(lesson_id),
    }
