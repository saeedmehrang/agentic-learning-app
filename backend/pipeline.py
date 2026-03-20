"""
Top-level agent pipeline.

Full pipeline: ContextAgent → LessonAgent → HelpAgent (conditional) → SummaryAgent

Phase 3 skeleton: only ContextAgent is wired in. Phase 4 will append
lesson_agent, help_agent, and summary_agent to sub_agents.
"""
from __future__ import annotations

from google.adk.agents import SequentialAgent

from agents.context_agent import context_agent

# Phase 4: uncomment and append remaining agents
# from agents.lesson_agent import lesson_agent
# from agents.help_agent import help_agent
# from agents.summary_agent import summary_agent

pipeline = SequentialAgent(
    name="learning_pipeline",
    description="ContextAgent → LessonAgent → HelpAgent → SummaryAgent",
    sub_agents=[context_agent],
)
