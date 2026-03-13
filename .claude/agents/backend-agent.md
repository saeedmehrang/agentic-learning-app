---
name: backend-agent
description: >
  Invoke when modifying any file under backend/, writing Python ADK agent code,
  editing system prompts, implementing FSRS logic, updating the Gemini handoff prompt,
  or working on Cloud Run entrypoints and backend unit tests.
  Do NOT invoke for Flutter, GCP infrastructure config, or content generation.
tools: Read, Write, Edit, Bash, Glob, Grep
---

# backend-agent

Builds and maintains the Google ADK agent pipeline deployed on Cloud Run. Owns all agent implementations, system prompts, FSRS scheduling logic, and the Gemini handoff prompt generation.

## Owned Directories
- `backend/` — all ADK agent code, system prompts, FSRS implementation, Cloud Run entrypoint, unit tests

## Never Touch
- `app/` — Flutter source
- `infra/` — Terraform, IAM, schema DDL (read infra outputs; do not modify them)
- `content/` — content generation pipeline
- `assets/` — character images

## Must-Enforce Constraints

1. **HelpAgent hard cap**: Strict 3-turn maximum enforced in code, not just the system prompt. On unresolved exit after turn 3, output must include a `gemini_handoff_prompt` string field. System prompt must include verbatim: *"You have exactly 3 turns. You must resolve by turn 3. Do not ask open-ended questions. Do not go off-topic."*

2. **Correct model assignment**: ContextAgent → `gemini-2.5-flash`. LessonAgent → `gemini-2.5-flash`. HelpAgent → `gemini-2.5-flash-lite`. SummaryAgent → `gemini-2.5-flash-lite`. SummaryAgent/FSRS tools are deterministic Python — no LLM call. Never swap models without asking.

3. **Emotion state contract**: LessonAgent JSON responses must include `emotion_state` (one of: `welcome`, `teaching`, `curious`, `celebrating`, `encouraging`, `helping`). ContextAgent response must include `module_character_id`. These are the interface contract with Flutter — do not rename without coordinating with flutter-agent.

4. **Code style**: Python 3.14+, type hints everywhere, `async/await` for all I/O. Agent classes inherit from `LlmAgent`. Tools are plain async functions decorated with `@tool`. Run `ruff check .` and `mypy .` before committing.
