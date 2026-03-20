"""
Unit tests for summary_agent.py.

Tests verify agent configuration and schema correctness WITHOUT calling the LLM.
LLM behaviour is covered by integration tests in Phase 4.
"""
from __future__ import annotations

from agents.summary_agent import SESSION_RECORD_SCHEMA, SUMMARY_AGENT_INSTRUCTION, summary_agent
from config import settings

# ---------------------------------------------------------------------------
# Tool registration tests
# ---------------------------------------------------------------------------


def test_run_fsrs_tool_registered() -> None:
    """run_fsrs tool must be registered on the summary_agent."""
    # Tools may be raw functions (.__name__) or ADK-wrapped objects (.name)
    tool_names = [
        getattr(t, "name", None) or getattr(t, "__name__", None)
        for t in summary_agent.tools
    ]
    assert "run_fsrs" in tool_names, (
        f"run_fsrs not found in agent tools; registered tools: {tool_names}"
    )


# ---------------------------------------------------------------------------
# SESSION_RECORD_SCHEMA tests
# ---------------------------------------------------------------------------


def test_session_record_schema_contains_all_required_fields() -> None:
    """SESSION_RECORD_SCHEMA must contain all required session fields."""
    required_fields = {
        "lesson_id",
        "tier_used",
        "quiz_scores",
        "time_on_task_seconds",
        "help_triggered",
        "gemini_handoff_used",
        "summary_text",
        "created_at",
    }
    missing = required_fields - SESSION_RECORD_SCHEMA.keys()
    assert not missing, f"SESSION_RECORD_SCHEMA is missing fields: {missing}"


def test_gemini_handoff_used_is_bool_type() -> None:
    """gemini_handoff_used must be bool in SESSION_RECORD_SCHEMA, not str or anything else."""
    assert SESSION_RECORD_SCHEMA["gemini_handoff_used"] is bool, (
        f"Expected bool for gemini_handoff_used, got {SESSION_RECORD_SCHEMA['gemini_handoff_used']}"
    )


def test_help_triggered_is_bool_type() -> None:
    """help_triggered must be bool in SESSION_RECORD_SCHEMA."""
    assert SESSION_RECORD_SCHEMA["help_triggered"] is bool, (
        f"Expected bool for help_triggered, got {SESSION_RECORD_SCHEMA['help_triggered']}"
    )


def test_schema_field_types_are_correct() -> None:
    """Spot-check remaining field types in SESSION_RECORD_SCHEMA."""
    assert SESSION_RECORD_SCHEMA["lesson_id"] is str
    assert SESSION_RECORD_SCHEMA["tier_used"] is str
    assert SESSION_RECORD_SCHEMA["quiz_scores"] is dict
    assert SESSION_RECORD_SCHEMA["time_on_task_seconds"] is int
    assert SESSION_RECORD_SCHEMA["summary_text"] is str
    assert SESSION_RECORD_SCHEMA["created_at"] is str


# ---------------------------------------------------------------------------
# Agent configuration tests
# ---------------------------------------------------------------------------


def test_agent_uses_summary_agent_model() -> None:
    """summary_agent must use settings.summary_agent_model."""
    assert summary_agent.model == settings.summary_agent_model, (
        f"Expected model {settings.summary_agent_model!r}, got {summary_agent.model!r}"
    )


def test_agent_output_key() -> None:
    """summary_agent must use 'summary_output' as output_key."""
    assert summary_agent.output_key == "summary_output"


# ---------------------------------------------------------------------------
# System prompt privacy constraint test
# ---------------------------------------------------------------------------


def test_system_prompt_mentions_gemini_handoff_boolean_constraint() -> None:
    """System prompt must state that gemini_handoff_used is boolean only (privacy constraint)."""
    prompt_lower = SUMMARY_AGENT_INSTRUCTION.lower()
    # Check that the prompt explicitly addresses the boolean-only constraint
    assert "boolean" in prompt_lower or "bool" in prompt_lower, (
        "System prompt must mention that gemini_handoff_used is boolean only"
    )
    assert "gemini_handoff_used" in SUMMARY_AGENT_INSTRUCTION, (
        "System prompt must reference gemini_handoff_used field by name"
    )
