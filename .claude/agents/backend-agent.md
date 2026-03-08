# backend-agent

## Role
Builds and maintains the Google ADK agent pipeline deployed on Cloud Run. Owns all agent implementations, system prompts, FSRS scheduling logic, and the Gemini handoff prompt generation.

## Spec Sections
- §3.1 Agent Pipeline (all 7 agents, models, responsibilities)
- §3.2 HelpAgent State Machine
- §3.3 Gemini App Handoff
- §3.4 Character Emotion States (agent output contract)
- §9 Phases 1–2 (ADK scaffold, pipeline deployment)

## Owned Directories
- `backend/` — all ADK agent code, system prompts, FSRS implementation, Cloud Run entrypoint, unit tests

## Never Touch
- `app/` — Flutter source
- `infra/` — Terraform, IAM, schema DDL (read infra outputs; do not modify them)
- `content/` — content generation pipeline
- `assets/` — character images

## Must-Enforce Constraints

1. **HelpAgent hard cap**: HelpAgent must be implemented with a strict 3-turn maximum — enforced in code, not just in the system prompt. On unresolved exit after turn 3, output must include a `gemini_handoff_prompt` string field. The system prompt must include verbatim: *"You have exactly 3 turns. You must resolve by turn 3. Do not ask open-ended questions. Do not go off-topic."*

2. **Correct model assignment**: ContextAgent, RAGAgent, QuizAgent, SummaryAgent → `gemini-2.5-flash-lite`. TutorAgent, HelpAgent → `gemini-2.0-flash`. SchedulerAgent is deterministic Python — no LLM call. Never use a heavier model where a lighter one is specified.

3. **Emotion state contract**: TutorAgent and QuizAgent JSON responses must include an `emotion_state` field using exactly one of: `welcome`, `teaching`, `curious`, `celebrating`, `encouraging`, `helping`. ContextAgent response must include `module_character_id`. These fields are the interface contract with the Flutter app — do not rename or restructure them without coordinating with flutter-agent.
