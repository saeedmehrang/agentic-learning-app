"""
Unit tests for lesson_agent.py.

Tests verify agent configuration without invoking the LLM:
  - search_knowledge_base tool is registered
  - System prompt contains all 4 quiz format names
  - System prompt contains all 6 emotion state names
  - System prompt contains the trigger_help string
  - Agent uses the correct model from settings
"""
from __future__ import annotations

from agents.lesson_agent import LESSON_AGENT_INSTRUCTION, lesson_agent
from config import settings

# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def test_search_knowledge_base_tool_registered() -> None:
    """search_knowledge_base must be registered in lesson_agent.tools."""
    tool_names = [
        t.name if hasattr(t, "name") else getattr(t, "__name__", str(t))
        for t in lesson_agent.tools
    ]
    assert "search_knowledge_base" in tool_names, (
        f"search_knowledge_base not found in lesson_agent.tools; found: {tool_names}"
    )


# ---------------------------------------------------------------------------
# System prompt — quiz format names
# ---------------------------------------------------------------------------


def test_system_prompt_contains_mc_format() -> None:
    """System prompt must name the 'mc' quiz format."""
    assert "mc" in LESSON_AGENT_INSTRUCTION


def test_system_prompt_contains_tf_format() -> None:
    """System prompt must name the 'tf' quiz format."""
    assert "tf" in LESSON_AGENT_INSTRUCTION


def test_system_prompt_contains_fill_format() -> None:
    """System prompt must name the 'fill' quiz format."""
    assert "fill" in LESSON_AGENT_INSTRUCTION


def test_system_prompt_contains_command_format() -> None:
    """System prompt must name the 'command' quiz format."""
    assert "command" in LESSON_AGENT_INSTRUCTION


def test_system_prompt_names_all_four_quiz_formats() -> None:
    """All four quiz format names must appear together in the system prompt."""
    for fmt in ("mc", "tf", "fill", "command"):
        assert fmt in LESSON_AGENT_INSTRUCTION, f"Quiz format '{fmt}' missing from system prompt"


# ---------------------------------------------------------------------------
# System prompt — emotion state names
# ---------------------------------------------------------------------------


def test_system_prompt_names_all_six_emotion_states() -> None:
    """All six valid emotion state values must appear in the system prompt."""
    for state in ("welcome", "teaching", "curious", "celebrating", "encouraging", "helping"):
        assert state in LESSON_AGENT_INSTRUCTION, (
            f"Emotion state '{state}' missing from system prompt"
        )


# ---------------------------------------------------------------------------
# System prompt — trigger_help string
# ---------------------------------------------------------------------------


def test_system_prompt_contains_trigger_help() -> None:
    """System prompt must contain the string 'trigger_help'."""
    assert "trigger_help" in LESSON_AGENT_INSTRUCTION


# ---------------------------------------------------------------------------
# Model assignment
# ---------------------------------------------------------------------------


def test_lesson_agent_uses_correct_model() -> None:
    """lesson_agent must use settings.lesson_agent_model."""
    assert lesson_agent.model == settings.lesson_agent_model, (
        f"Expected model {settings.lesson_agent_model!r}, got {lesson_agent.model!r}"
    )
