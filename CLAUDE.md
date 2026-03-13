# Adaptive Learning App — Project Briefing

Self-paced agentic learning platform. MVP: Linux basics course (9 modules, 29 lessons). Flutter mobile app + Google ADK backend + GCP-native data layer. Full spec: `learning_system_spec.md`.

## Tech Stack

- **Frontend**: Flutter (Dart) — iOS & Android
- **Backend**: Google ADK agents on Cloud Run (Python 3.11+)
- **Databases**: Cloud SQL (PostgreSQL + pgvector), Firestore
- **Auth & Analytics**: Firebase Auth (anonymous → Google Sign-In upgrade), Firebase Analytics, Crashlytics
- **AI Models**: Gemini 2.0 Flash (ContextAgent, LessonAgent), Gemini 2.5 Flash-Lite (HelpAgent, SummaryAgent)
- **Spaced Repetition**: FSRS algorithm
- **Infra**: GCP-native, scale-to-zero Cloud Run

## Directory Structure

```
infra/           GCP setup, Cloud SQL schema, Firestore schema, Cloud Run config, IAM
backend/
  agents/        ContextAgent, LessonAgent, HelpAgent, SummaryAgent
  tools/         search_knowledge_base.py, run_fsrs.py
  content/       Course content (Linux Basics)
app/             Flutter source (lib/), Firebase config, pubspec.yaml
  lib/
    screens/     UI screens
    widgets/     Reusable widgets
    services/    API calls to backend
assets/
  characters/    48 character PNGs (8 characters × 6 emotions), style anchor prompts
content/         One-time lesson/quiz generation pipeline, approved content exports
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

## Architecture: 4-Agent Pipeline

```
ContextAgent → LessonAgent → HelpAgent → SummaryAgent
```

- **ContextAgent**: Retrieves user memory (Firestore) + calls `search_knowledge_base` tool (pgvector RAG). Model: Gemini 2.0 Flash.
- **LessonAgent**: Delivers lessons AND handles quizzing (single context window); calls difficulty tiers (Beginner / Intermediate / Advanced). Model: Gemini 2.0 Flash.
- **HelpAgent**: Answers follow-up questions; 3-turn limit, then hands off to Gemini app via `gemini_handoff_prompt`. Model: Gemini 2.5 Flash-Lite.
- **SummaryAgent**: Evaluates session, calls `run_fsrs` tool to update spaced repetition schedule. Model: Gemini 2.5 Flash-Lite.

Agents are `LlmAgent` classes from Google ADK. Python tools (not agents) handle RAG (`search_knowledge_base`) and FSRS (`run_fsrs`).

## Cross-Cutting Constraints

1. **Frugal by design.** Target ≤$12/month at 100 learners. Cloud Run must be scale-to-zero. Cloud SQL must be `db-f1-micro`. Flag any change that meaningfully increases per-session cost.
2. **GCP-native only.** No third-party infra, no self-hosted services. Approved stack: Cloud SQL, Firestore, Cloud Run, Firebase.
3. **HelpAgent hard cap.** 3 turns maximum — enforced in code. Always outputs `gemini_handoff_prompt` on unresolved exit. Never extend this limit.
4. **SchedulerAgent / SummaryAgent tools have no LLM.** `run_fsrs` is pure Python. Never add a model call to FSRS logic.
5. **Character assets are local-bundle only.** No network image loading at runtime. Static PNG + cross-fade transition only — no animated UI for characters.
6. **Anonymous-first auth.** Never block content behind sign-in. Google Sign-In offered after session 3 only.
7. **Privacy.** Never log `gemini_handoff_prompt` content in analytics. Track `gemini_handoff_used` as boolean only. Never store user PII in Cloud SQL — user data lives in Firestore keyed by anonymous Firebase UID.
8. **Do NOT add new agents** without being explicitly asked. The 4-agent pipeline is intentional. Prefer tools (plain async Python functions) over agents for any task that doesn't need its own LLM call.
9. **Model assignment is fixed.** Do not swap Gemini models across agents without asking.

## Essential Commands

```bash
# Flutter
flutter run              # run on connected device/emulator
flutter test             # run all tests
flutter analyze          # static analysis (run before committing)
flutter build apk        # Android build
flutter build ios        # iOS build

# Backend (Python / ADK)
python -m pytest         # run backend tests
ruff check .             # lint
mypy .                   # type check
gcloud run deploy        # deploy agent service to Cloud Run
gcloud builds submit     # trigger Cloud Build

# Database
alembic upgrade head                              # apply migrations
alembic revision --autogenerate -m "description" # generate migration
```

## Code Style

### Dart / Flutter
- Prefer `StatelessWidget`; use `riverpod` for state management
- File naming: `snake_case.dart`
- Prefer named parameters for constructors with 2+ arguments
- No hardcoded user-visible strings — use localization keys
- Run `flutter analyze` and fix all warnings before considering a task done
- Don't install new Flutter packages without checking pub.dev score and maintenance status

### Python (Backend / Agents)
- Python 3.14+, type hints everywhere
- Use `async/await` for all I/O (Cloud Run, Firestore, Cloud SQL)
- Agent classes inherit from `LlmAgent` (Google ADK)
- Tools are plain async Python functions decorated with `@tool`
- Follow existing agent structure exactly
- Run `ruff check .` and `mypy .` before committing

## Database Patterns

- pgvector queries: always use `LIMIT` and filter by `course_id` before vector similarity search
- Firestore: user memory documents keyed by `user_id`; keep documents small (< 1MB)
- Never do N+1 queries — use joins or batch fetches
- All Cloud SQL access goes through connection pool (not direct connections from agents)

## Testing Requirements

- New Flutter widgets: add a widget test in `app/test/widgets/`
- New agent logic: add a unit test mocking the LLM response
- New tools (`search_knowledge_base`, `run_fsrs`): test with representative inputs
- Integration tests go in `app/test/integration/` — run these before any Cloud Run deploy

## Git Workflow

- Branch naming: `feature/`, `fix/`, `chore/` prefixes
- Never commit directly to `main`
- Commit messages: imperative mood, max 72 chars (e.g. `Add quiz retry logic to LessonAgent`)
- Before opening a PR: `flutter analyze` + `pytest` must pass
