# Linux Learning App

Self-paced mobile learning platform for Linux basics. Bite-sized 7–10 minute sessions, spaced-repetition scheduling (FSRS), and short AI-assisted help conversations with a hard cap of 3 turns.

## Stack

| Layer | Technology |
|---|---|
| Mobile | Flutter (iOS + Android), Firebase Auth + Analytics |
| Backend | Google ADK agents on Cloud Run (scale-to-zero) |
| Knowledge store | Cloud SQL for PostgreSQL + pgvector |
| Learner memory | Firestore |

## Repository Layout

```
infra/        GCP infrastructure — Cloud SQL schema, Firestore rules, Cloud Run config, IAM
backend/      Google ADK agents, FSRS scheduler, system prompts
content/      Lesson & quiz generation pipeline, approved content exports
assets/
  characters/ 48 character PNGs (8 characters × 6 emotions)
app/          Flutter source, Firebase config
```

## Key Design Constraints

- **≤ $12/month** at 100 active learners — every architectural choice is costed against this.
- **GCP-native only** — no third-party infra or self-hosted services.
- **Anonymous-first auth** — no sign-in wall; Google Sign-In offered after session 3.
- **HelpAgent hard cap** — 3 turns maximum; unresolved exits produce a `gemini_handoff_prompt`.
- **SchedulerAgent is LLM-free** — pure Python FSRS, no model calls.
- **Character assets bundled locally** — no runtime network image loading.

## Full Specification

See [learning_system_spec.md](learning_system_spec.md) for the complete technical spec.
