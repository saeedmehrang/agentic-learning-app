"""
HelpAgent — answers follow-up questions from the learner during a lesson session.

Responsibilities:
- Answer clarifying questions raised by the learner after LessonAgent output
- Hard 3-turn cap enforced in code (HELP_AGENT_MAX_TURNS constant)
- On resolved exit: output { resolved: true, character_emotion_state: "celebrating" }
- On unresolved exit at turn 3: output { resolved: false, gemini_handoff_prompt: "<contextual>" }
- State machine: IDLE → ACTIVE → RESOLVED
"""
from __future__ import annotations

import logging

from google.adk.agents import LlmAgent

from config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hard cap — module-level constant, NOT configurable
# ---------------------------------------------------------------------------

HELP_AGENT_MAX_TURNS = 3

# ---------------------------------------------------------------------------
# State machine states
# ---------------------------------------------------------------------------

IDLE = "IDLE"
ACTIVE = "ACTIVE"
RESOLVED = "RESOLVED"

# ---------------------------------------------------------------------------
# System instruction
# ---------------------------------------------------------------------------

HELP_AGENT_INSTRUCTION = """
You are the HelpAgent for an adaptive Linux learning platform. Your role is to
answer follow-up questions from the learner concisely and accurately.

## Hard turn limit

You have exactly 3 turns. You must resolve or declare unresolved by turn 3.
Do not ask open-ended questions. Do not go off-topic.

## Resolution rules

- **Resolved** (any turn): If the learner's question is answered satisfactorily,
  respond with ONLY this JSON:
  { "resolved": true, "character_emotion_state": "celebrating" }

- **Unresolved at turn 3**: If the question cannot be resolved within 3 turns,
  respond with ONLY this JSON (fill in a contextually accurate handoff prompt —
  do NOT use a generic template):
  { "resolved": false, "gemini_handoff_prompt": "<contextual prompt string>" }

The gemini_handoff_prompt must give the Gemini app enough context to continue
helping the learner without any of this conversation history. Make it specific
to the exact question the learner asked.

## Behaviour rules

- Answer directly. No preamble.
- If you need clarification, ask ONE targeted yes/no or multiple-choice question
  (counts as one of your 3 turns).
- Never extend beyond 3 turns under any circumstance.
- Always output valid JSON at resolution (resolved or unresolved at turn 3).
"""

# ---------------------------------------------------------------------------
# HelpAgentRunner — enforces the hard 3-turn cap in code
# ---------------------------------------------------------------------------


class HelpAgentRunner:
    """
    Wraps HelpAgent to enforce the hard turn-count cap in code.

    The system prompt alone cannot be trusted to enforce the cap — this class
    provides the authoritative turn counter.

    State machine: IDLE → ACTIVE → RESOLVED
    """

    def __init__(self) -> None:
        self.turn_count: int = 0
        self.state: str = IDLE

    def increment_turn(self) -> int:
        """
        Increment the turn counter and update state.

        Returns:
            The new turn count after incrementing.

        Raises:
            RuntimeError: If called when turn_count already equals or exceeds
                HELP_AGENT_MAX_TURNS (hard cap enforcement).
        """
        if self.turn_count >= HELP_AGENT_MAX_TURNS:
            raise RuntimeError(
                f"HelpAgent hard cap exceeded: already at {self.turn_count} turns "
                f"(max={HELP_AGENT_MAX_TURNS}). Force-resolve this interaction."
            )
        self.turn_count += 1
        self.state = ACTIVE
        return self.turn_count

    def is_at_cap(self) -> bool:
        """Return True if the runner has reached the hard turn cap."""
        return self.turn_count >= HELP_AGENT_MAX_TURNS

    def resolve(self) -> None:
        """Mark the interaction as resolved."""
        self.state = RESOLVED

    # Privacy note: NEVER log gemini_handoff_prompt content here or anywhere
    # in this module. Only log the boolean gemini_handoff_used.
    def log_resolution(self, *, resolved: bool) -> None:
        """
        Log resolution outcome.

        IMPORTANT: Only the boolean outcome is logged. The gemini_handoff_prompt
        content must NEVER appear in logs (privacy constraint from CLAUDE.md).
        """
        logger.info(
            "HelpAgent resolution",
            extra={
                "gemini_handoff_used": not resolved,
                "turn_count": self.turn_count,
            },
        )
        self.resolve()


# ---------------------------------------------------------------------------
# Agent definition
# ---------------------------------------------------------------------------

help_agent = LlmAgent(
    name="help_agent",
    model=settings.help_agent_model,
    instruction=HELP_AGENT_INSTRUCTION,
    output_key="help_output",
)
