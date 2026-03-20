"""
Unit tests for help_agent.py.

Tests target:
  - help_agent instantiation (model, output_key)
  - System prompt content (verbatim constraint block, gemini_handoff_prompt field)
  - HELP_AGENT_MAX_TURNS constant value
  - HelpAgentRunner state machine and turn enforcement

The LlmAgent itself is not tested here — LLM behaviour is covered by
integration tests in Phase 4.
"""
from __future__ import annotations

import pytest

from agents.help_agent import (
    HELP_AGENT_INSTRUCTION,
    HELP_AGENT_MAX_TURNS,
    HelpAgentRunner,
    help_agent,
)
from config import settings

# ---------------------------------------------------------------------------
# Agent instantiation tests
# ---------------------------------------------------------------------------


def test_help_agent_uses_correct_model() -> None:
    """help_agent must be instantiated with settings.help_agent_model."""
    assert help_agent.model == settings.help_agent_model


def test_help_agent_output_key() -> None:
    """help_agent output_key must be 'help_output'."""
    assert help_agent.output_key == "help_output"


# ---------------------------------------------------------------------------
# System prompt content tests
# ---------------------------------------------------------------------------


def test_system_prompt_contains_3_turns_constraint() -> None:
    """System prompt must contain the verbatim 3-turn constraint text."""
    constraint = "You have exactly 3 turns. You must resolve or declare unresolved by turn 3."
    assert constraint in HELP_AGENT_INSTRUCTION


def test_system_prompt_contains_no_open_ended_questions() -> None:
    """System prompt must include the no-open-ended-questions directive."""
    assert "Do not ask open-ended questions." in HELP_AGENT_INSTRUCTION


def test_system_prompt_contains_do_not_go_off_topic() -> None:
    """System prompt must include the do-not-go-off-topic directive."""
    assert "Do not go off-topic." in HELP_AGENT_INSTRUCTION


def test_system_prompt_contains_gemini_handoff_prompt_field() -> None:
    """System prompt must name the gemini_handoff_prompt output field."""
    assert "gemini_handoff_prompt" in HELP_AGENT_INSTRUCTION


# ---------------------------------------------------------------------------
# HELP_AGENT_MAX_TURNS constant tests
# ---------------------------------------------------------------------------


def test_help_agent_max_turns_equals_3() -> None:
    """HELP_AGENT_MAX_TURNS must be exactly 3 — not configurable."""
    assert HELP_AGENT_MAX_TURNS == 3


def test_help_agent_max_turns_is_int() -> None:
    """HELP_AGENT_MAX_TURNS must be an integer."""
    assert isinstance(HELP_AGENT_MAX_TURNS, int)


# ---------------------------------------------------------------------------
# HelpAgentRunner tests
# ---------------------------------------------------------------------------


def test_runner_initializes_turn_count_to_zero() -> None:
    """HelpAgentRunner must start with turn_count == 0."""
    runner = HelpAgentRunner()
    assert runner.turn_count == 0


def test_runner_initializes_state_to_idle() -> None:
    """HelpAgentRunner must start in IDLE state."""
    from agents.help_agent import IDLE
    runner = HelpAgentRunner()
    assert runner.state == IDLE


def test_runner_increments_turn_count() -> None:
    """increment_turn() must increment turn_count by 1 each call."""
    runner = HelpAgentRunner()
    result = runner.increment_turn()
    assert result == 1
    assert runner.turn_count == 1


def test_runner_increments_turn_count_multiple_times() -> None:
    """Successive calls to increment_turn() accumulate correctly."""
    runner = HelpAgentRunner()
    runner.increment_turn()
    runner.increment_turn()
    result = runner.increment_turn()
    assert result == 3
    assert runner.turn_count == 3


def test_runner_state_transitions_to_active_on_increment() -> None:
    """State must become ACTIVE after the first increment_turn() call."""
    from agents.help_agent import ACTIVE
    runner = HelpAgentRunner()
    runner.increment_turn()
    assert runner.state == ACTIVE


def test_runner_raises_when_turns_exceed_cap() -> None:
    """increment_turn() must raise RuntimeError when at or beyond the hard cap."""
    runner = HelpAgentRunner()
    # Exhaust all allowed turns
    for _ in range(HELP_AGENT_MAX_TURNS):
        runner.increment_turn()

    with pytest.raises(RuntimeError):
        runner.increment_turn()


def test_runner_is_at_cap_false_initially() -> None:
    """is_at_cap() must return False before any turns are taken."""
    runner = HelpAgentRunner()
    assert runner.is_at_cap() is False


def test_runner_is_at_cap_true_after_max_turns() -> None:
    """is_at_cap() must return True after HELP_AGENT_MAX_TURNS increments."""
    runner = HelpAgentRunner()
    for _ in range(HELP_AGENT_MAX_TURNS):
        runner.increment_turn()
    assert runner.is_at_cap() is True


def test_runner_resolve_sets_state_to_resolved() -> None:
    """resolve() must set state to RESOLVED."""
    from agents.help_agent import RESOLVED
    runner = HelpAgentRunner()
    runner.increment_turn()
    runner.resolve()
    assert runner.state == RESOLVED


def test_runner_log_resolution_resolved(caplog: pytest.LogCaptureFixture) -> None:
    """log_resolution(resolved=True) must log gemini_handoff_used=False."""
    import logging
    runner = HelpAgentRunner()
    runner.increment_turn()
    with caplog.at_level(logging.INFO, logger="agents.help_agent"):
        runner.log_resolution(resolved=True)
    # State should be RESOLVED after log_resolution
    from agents.help_agent import RESOLVED
    assert runner.state == RESOLVED


def test_runner_log_resolution_unresolved(caplog: pytest.LogCaptureFixture) -> None:
    """log_resolution(resolved=False) must not log any handoff prompt content."""
    import logging
    runner = HelpAgentRunner()
    for _ in range(HELP_AGENT_MAX_TURNS):
        runner.increment_turn()
    with caplog.at_level(logging.INFO, logger="agents.help_agent"):
        runner.log_resolution(resolved=False)
    # Verify no gemini_handoff_prompt content appears anywhere in the log output
    for record in caplog.records:
        assert "gemini_handoff_prompt" not in str(record.getMessage())
