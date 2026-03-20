"""
ContextAgent — reads Firestore learner state and determines the next study session.

Responsibilities:
- Read learner profile, concept schedule, and last session from Firestore
- Determine the next concept to study (lowest next_review_at or lowest mastery_score)
- Assign the module character based on the concept's module
- Output structured JSON: { next_concept_id, difficulty_tier, module_character_id, session_goal }
"""
from __future__ import annotations

import logging
from typing import Any

from google.adk.agents import LlmAgent
from google.cloud import firestore as _fs
from pydantic import BaseModel

from config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module → character mapping (one character per module, 9 modules total)
# ---------------------------------------------------------------------------

MODULE_CHARACTER: dict[int, str] = {
    1: "tux_jr",
    2: "cursor",
    3: "filo",
    4: "snippy",
    5: "keyra",
    6: "spinner",
    7: "wavo",
    8: "boxby",
    9: "scrippy",
}

# ---------------------------------------------------------------------------
# Firestore client singleton
# ---------------------------------------------------------------------------

_firestore_client: _fs.AsyncClient | None = None


def _get_firestore() -> _fs.AsyncClient:
    global _firestore_client
    if _firestore_client is None:
        _firestore_client = _fs.AsyncClient(project=settings.gcp_project_id)
        logger.info("Firestore async client initialized")
    return _firestore_client


# ---------------------------------------------------------------------------
# Firestore read tool — auto-wrapped by ADK when passed in tools=[]
# ---------------------------------------------------------------------------


async def read_learner_context(uid: str) -> dict[str, Any]:
    """
    Read learner profile, concept schedule, and most recent session from Firestore.

    Args:
        uid: Firebase anonymous UID for the learner.

    Returns:
        Dict with keys:
          uid, difficulty_tier, onboarding_complete, concepts (list), last_session (dict).
        Timestamps are converted to ISO 8601 strings for LLM consumption.
    """
    db = _get_firestore()

    # --- Profile: learners/{uid} document holds profile fields directly ---
    profile_ref = db.collection("learners").document(uid)
    profile_snap = await profile_ref.get()
    profile: dict[str, Any] = profile_snap.to_dict() or {}

    # --- Concepts: learners/{uid}/concepts/ sub-collection ---
    concepts_ref = db.collection("learners").document(uid).collection("concepts")
    concept_snaps = await concepts_ref.get()
    concepts: list[dict[str, Any]] = []
    for snap in concept_snaps:
        data: dict[str, Any] = snap.to_dict() or {}
        data["concept_id"] = snap.id
        # Convert Firestore Timestamps → ISO strings so the LLM can parse them
        for ts_field in ("next_review_at", "last_review_at"):
            val = data.get(ts_field)
            if val is not None and hasattr(val, "isoformat"):
                data[ts_field] = val.isoformat()
        concepts.append(data)

    # --- Most recent session: learners/{uid}/sessions/ ordered by created_at ---
    sessions_query = (
        db.collection("learners")
        .document(uid)
        .collection("sessions")
        .order_by("created_at", direction=_fs.Query.DESCENDING)
        .limit(1)
    )
    session_snaps = await sessions_query.get()
    last_session: dict[str, Any] = {}
    if session_snaps:
        last_session = session_snaps[0].to_dict() or {}

    logger.info(
        "read_learner_context complete",
        extra={"uid": uid, "concept_count": len(concepts)},
    )
    return {
        "uid": uid,
        "difficulty_tier": profile.get("difficulty_tier", "beginner"),
        "onboarding_complete": profile.get("onboarding_complete", False),
        "concepts": concepts,
        "last_session": last_session,
    }


# ---------------------------------------------------------------------------
# ContextAgent output schema
# ---------------------------------------------------------------------------


class ContextOutput(BaseModel):
    next_concept_id: str
    difficulty_tier: str
    module_character_id: str
    session_goal: str


# ---------------------------------------------------------------------------
# System instruction
# ---------------------------------------------------------------------------

_MODULE_CHARACTER_STR = ", ".join(f"{k}→{v}" for k, v in sorted(MODULE_CHARACTER.items()))

CONTEXT_AGENT_INSTRUCTION = f"""
You are the ContextAgent for an adaptive Linux learning platform.

Your job is to determine what the learner should study next in their session.

## Step 1 — Fetch learner data
Call the `read_learner_context` tool with the uid provided in the user message.

## Step 2 — Pick the next concept
From the returned `concepts` list:
- If the list is empty: set next_concept_id to "L01" (first lesson).
- Otherwise pick the concept with the earliest `next_review_at` that is in the past
  (before the current UTC time). If all `next_review_at` values are in the future,
  pick the concept with the lowest `mastery_score`.

## Step 3 — Assign the module character
The lesson ID format is "L##" where ## is the lesson number (01–29). Map the lesson
to its module using this approximate course structure (9 modules, ~3 lessons each):
- Lessons L01–L03 → Module 1
- Lessons L04–L06 → Module 2
- Lessons L07–L09 → Module 3
- Lessons L10–L12 → Module 4
- Lessons L13–L16 → Module 5
- Lessons L17–L20 → Module 6
- Lessons L21–L23 → Module 7
- Lessons L24–L26 → Module 8
- Lessons L27–L29 → Module 9

Module character mapping: {_MODULE_CHARACTER_STR}

## Step 4 — Set session goal
Write a single sentence describing what the learner will accomplish this session
(e.g. "Master file permission bits and the chmod command.").

## Output
Respond with ONLY a valid JSON object matching this schema — no other text:
{{
  "next_concept_id": "<lesson ID, e.g. L04>",
  "difficulty_tier": "<beginner|intermediate|advanced from learner profile>",
  "module_character_id": "<character ID from mapping>",
  "session_goal": "<one sentence>"
}}
"""

# ---------------------------------------------------------------------------
# Agent definition
# ---------------------------------------------------------------------------

context_agent = LlmAgent(
    name="context_agent",
    model=settings.context_agent_model,
    instruction=CONTEXT_AGENT_INSTRUCTION,
    tools=[read_learner_context],
    output_schema=ContextOutput,
    output_key="context_output",
)
