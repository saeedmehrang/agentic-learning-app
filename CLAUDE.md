# Linux Learning App — Project Briefing

Self-paced agentic learning platform. MVP: Linux basics course. Flutter mobile app + Google ADK backend + GCP-native data layer. Full spec: `learning_system_spec.md`.

## Directory Structure

```
infra/           GCP setup, Cloud SQL schema, Firestore schema, Cloud Run config, IAM
backend/         Google ADK agents, FSRS scheduler, system prompts
content/         One-time lesson/quiz generation pipeline, approved content exports
assets/
  characters/    48 character PNGs (8 characters × 6 emotions), style anchor prompts
app/             Flutter source, Firebase config, pubspec.yaml
```

## Sub-Agents

Use `.claude/agents/` sub-agents for any focused work in their domain:

| Agent | Invoke when working on… |
|---|---|
| `infra-agent` | Cloud SQL schema, Firestore schema, Cloud Run deployment, IAM, secrets |
| `content-agent` | Lesson generation, quiz generation, embedding pipeline, DB ingestion |
| `character-agent` | Gemini image prompts, style anchors, PNG export, asset consistency |
| `backend-agent` | ADK agents, system prompts, FSRS logic, Gemini handoff prompt |
| `flutter-agent` | Screens, state, character widget, Firebase Auth/Analytics, Gemini referral card |

## Cross-Cutting Constraints

1. **Frugal by design.** Target ≤$12/month at 100 learners. No architectural choice that breaks this. Cloud Run must be scale-to-zero. Cloud SQL must be `db-f1-micro`.
2. **GCP-native only.** No third-party infra, no self-hosted services.
3. **HelpAgent hard cap.** 3 turns maximum — enforced in code. Always outputs `gemini_handoff_prompt` on unresolved exit.
4. **SchedulerAgent has no LLM.** Pure Python FSRS. Never add a model call here.
5. **Character assets are local-bundle only.** No network image loading at runtime.
6. **Anonymous-first auth.** Never block content behind sign-in. Google Sign-In offered after session 3 only.
7. **Privacy.** Never log `gemini_handoff_prompt` content in analytics. Track `gemini_handoff_used` as boolean only.
