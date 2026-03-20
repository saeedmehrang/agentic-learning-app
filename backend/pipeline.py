"""
Top-level agent pipeline.

Full pipeline: ContextAgent → LessonAgent → HelpAgent (conditional) → SummaryAgent

Conditional routing note
------------------------
ADK's SequentialAgent runs all sub_agents unconditionally in order. The HelpAgent
is included in the sub_agents list so ADK is aware of it, but conditional execution
(only when LessonAgent emits trigger_help: true) is enforced by the caller in
main.py using HelpAgentRunner. On a session where trigger_help is absent or false,
the HelpAgent step is skipped by the runner before it reaches the ADK event loop.

See main.py::session_start for the routing implementation.
"""
from __future__ import annotations

from google.adk.agents import SequentialAgent

from agents.context_agent import context_agent
from agents.help_agent import help_agent
from agents.lesson_agent import lesson_agent
from agents.summary_agent import summary_agent

pipeline = SequentialAgent(
    name="learning_pipeline",
    description="ContextAgent → LessonAgent → HelpAgent (conditional) → SummaryAgent",
    sub_agents=[context_agent, lesson_agent, help_agent, summary_agent],
)
