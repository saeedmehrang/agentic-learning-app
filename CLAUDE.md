# Adaptive Learning App — Project Briefing

Self-paced agentic learning platform. MVP: Linux basics course (9 modules, 29 lessons). Flutter mobile app + direct Gemini SDK backend + GCP-native data layer. Full spec: `learning_system_spec.md`.

## Tech Stack

- **Frontend**: Flutter (Dart) — iOS & Android
- **Backend**: FastAPI on Cloud Run (Python 3.11+), direct Gemini SDK (no ADK framework)
- **Database**: Firestore only — learner state, FSRS concept schedules, session records
- **Content storage**: GCS bucket (`agentic-learning-pipeline`) — approved lesson JSON files, `outlines.yaml`, `concept_map.json`. Loaded into memory at startup; no per-request file I/O.
- **Auth & Analytics**: Firebase Auth (anonymous → Google Sign-In upgrade), Firebase Analytics, Crashlytics
- **AI Models**: Gemini 3.1 Flash-Lite (`gemini-3.1-flash-lite-preview`, `location="global"`) for all chat components
- **Spaced Repetition**: FSRS algorithm (pure Python, no LLM)
- **Infra**: GCP-native, scale-to-zero Cloud Run. No Cloud SQL. No pgvector. No ADK.

## Directory Structure

```
infra/               GCP provisioning scripts, Cloud Run config, IAM, Firestore schema
backend/
  main.py            FastAPI app — all HTTP endpoints
  lesson_session.py  LessonSession + HelpSession (stateful Gemini chats)
  summary_call.py    SummaryCall (single generate_content call + FSRS + Firestore write)
  scheduler.py       Pure-Python lesson scheduler (reads Firestore, picks next lesson)
  cache_manager.py   Gemini context cache builder (disabled by default)
  config.py          Settings via pydantic-settings (env vars + Secret Manager)
  run_fsrs.py        FSRS-4 algorithm — deterministic, no LLM
  storage.py         GCS + local filesystem backends for content loading
  tests/
    integration/     End-to-end tests against live Cloud Run (require CLOUD_RUN_URL)
app/                 Flutter source (lib/), Firebase config, pubspec.yaml
  lib/
    screens/         UI screens
    widgets/         Reusable widgets (CharacterWidget, GeminiReferralCard, …)
    services/        HTTP client to Cloud Run backend
assets/
  characters/        54 character PNGs (9 characters × 6 emotions), style anchor prompts
content/             One-time lesson/quiz generation pipeline scripts
courses/
  linux-basics/
    pipeline/approved/  87 approved lesson JSON files (29 lessons × 3 tiers)
    outlines.yaml       Course structure — lesson titles, prereqs, module mapping
    concept_map.json    Cross-lesson concept relationships
dev_chat/            Streamlit UI for manual backend smoke testing (not shipped)
notes/               Architecture decisions, deployment instructions, phase plans
```

## Sub-Agents

Use `.claude/agents/` sub-agents for any focused work in their domain:

| Agent | Invoke when working on… |
|---|---|
| `infra-agent` | Firestore schema, Cloud Run config, IAM, Secret Manager |
| `content-agent` | Lesson/quiz generation pipeline, `generate_content.py`, approved content |
| `character-agent` | Gemini image prompts, style anchors, PNG export, asset consistency |
| `backend-agent` | `lesson_session.py`, `summary_call.py`, `scheduler.py`, FSRS, Gemini handoff |
| `flutter-agent` | Screens, state, CharacterWidget, Firebase Auth/Analytics, GeminiReferralCard |

## Architecture: Direct Gemini SDK Pipeline

No ADK. No pgvector. No Cloud SQL. Three Gemini SDK calls per session:

```
POST /session/start  →  scheduler.pick_next_lesson()  →  LessonSession (multi-turn chat)
                                                       ↓ trigger_help
                                                       HelpSession (up to 3 turns)
POST /session/complete  →  SummaryCall (single call)  →  run_fsrs()  →  Firestore write
```

| Component | Type | Model | Responsibility |
|---|---|---|---|
| `scheduler.pick_next_lesson()` | Pure Python | None | Read Firestore FSRS schedule; pick next lesson + tier |
| `LessonSession` | `client.chats.create()` | Gemini 3.1 Flash-Lite, thinking=LOW (1024) | Deliver lesson; run quiz loop; emit `trigger_help` on 2nd consecutive wrong answer |
| `HelpSession` | `client.chats.create()` | Gemini 3.1 Flash-Lite, thinking=MINIMAL (0) | 3-turn clarification; outputs `gemini_handoff_prompt` on unresolved exit |
| `SummaryCall` | `generate_content()` | Gemini 3.1 Flash-Lite, thinking=MINIMAL (0) | Summarise session; call `run_fsrs()`; write Firestore session + concept records |

All `genai.Client` instantiations use `genai.Client(vertexai=True, location="global")` — required for the `gemini-3.1-flash-lite-preview` preview model which is only available via the global Vertex AI endpoint.

## Session HTTP API

```
POST /session/start                    → {session_id, lesson_id, tier, character_id, status}
GET  /session/{id}/lesson              → {lesson_text, character_emotion_state, key_concepts}
GET  /session/{id}/quiz/question       → {question_text, format, options, character_emotion_state}
POST /session/{id}/quiz/answer         → {correct, explanation, trigger_help, character_emotion_state}
POST /session/{id}/help                → {resolved, reply_text, character_emotion_state, gemini_handoff_prompt?}
POST /session/{id}/complete            → {summary: {summary_text, gemini_handoff_used, ...}}
GET  /health                           → {status: "ok"}
```

Phase transitions: `lesson → quiz → help → quiz → … → complete`. Calling an endpoint out of phase returns `409`.

## Cross-Cutting Constraints

1. **Frugal by design.** Target ≤$12/month at 100 learners. Cloud Run must be scale-to-zero. Flag any change that meaningfully increases per-session cost.
2. **GCP-native only.** No third-party infra. Approved stack: Firestore, GCS, Cloud Run, Firebase. No Cloud SQL, no pgvector, no ADK.
3. **HelpSession hard cap.** 3 turns maximum — enforced in Python (`RuntimeError` on 4th call → 409 HTTP). Always outputs `gemini_handoff_prompt` on unresolved exit. Never extend this limit.
4. **FSRS has no LLM.** `run_fsrs()` is pure deterministic Python. Never add a model call to FSRS logic.
5. **Character assets are local-bundle only.** No network image loading at runtime. Static PNG + `AnimatedCrossFade` (300ms) only.
6. **Anonymous-first auth.** Never block content behind sign-in. Google Sign-In offered after session 3 only.
7. **Privacy.** Never log `gemini_handoff_prompt` content. Track `gemini_handoff_used` as boolean only. No PII in Firestore — all data keyed by anonymous Firebase UID.
8. **Do NOT add new pipeline components** without being explicitly asked. The scheduler + LessonSession + HelpSession + SummaryCall pipeline is intentional and complete.
9. **Model assignment is fixed.** All chat components use `gemini-3.1-flash-lite-preview`. Do not swap without asking. Thinking budgets: LessonSession=1024 (LOW), HelpSession=0 (MINIMAL), SummaryCall=0 (MINIMAL).

## Essential Commands

```bash
# Backend — always activate venv first
source backend/.venv/bin/activate
python -m pytest backend/tests/ --ignore=backend/tests/integration -q   # unit tests
ruff check backend/                                                        # lint
CLOUD_RUN_URL=https://backend-1081017476491.us-central1.run.app \
  python -m pytest backend/tests/integration/test_session_e2e.py -v      # integration tests

# Cloud Run deploy
gcloud builds submit --config infra/cloudbuild/backend.yaml \
  --substitutions=COMMIT_SHA=$(git rev-parse --short HEAD) .
gcloud run deploy backend \
  --image us-central1-docker.pkg.dev/agentic-learning-app-e13cb/agentic-learning/backend:latest \
  --region us-central1 --platform managed --allow-unauthenticated \
  --min-instances 0 \
  --service-account cloud-run-app-identity@agentic-learning-app-e13cb.iam.gserviceaccount.com \
  --set-env-vars APP_ENV=production,GCP_PROJECT_ID=agentic-learning-app-e13cb,GCP_LOCATION=us-central1,GCS_PIPELINE_BUCKET=agentic-learning-pipeline,ENABLE_LESSON_CACHE=false,APP_VERSION=$(git rev-parse --short HEAD)

# Flutter (run on host machine, not devcontainer)
flutter run              # run on connected device/emulator
flutter test             # run all tests
flutter analyze          # static analysis — must be zero warnings before commit
flutter build apk        # Android build
flutter build ios        # iOS build

# Manual smoke test
cd dev_chat && streamlit run app.py   # point BACKEND_URL at Cloud Run URL
```

## Code Style

### Python (Backend)
- Python 3.11+, type hints everywhere
- `async/await` for all I/O (Firestore, GCS, Gemini SDK)
- No ADK, no LlmAgent, no @tool decorators — plain async functions
- Gemini SDK: `google.genai` package (`genai.Client(vertexai=True, location="global")`)
- Run `ruff check .` before committing. Type checker is `ty` (Astral), not mypy.
- Always activate `.venv` before running Python tools: `source backend/.venv/bin/activate`

### Dart / Flutter
- Prefer `StatelessWidget`; use `riverpod` for state management
- File naming: `snake_case.dart`
- Prefer named parameters for constructors with 2+ arguments
- No hardcoded user-visible strings — use localization keys
- Run `flutter analyze` and fix all warnings before considering a task done
- Don't install new Flutter packages without checking pub.dev score and maintenance status

## Firestore Schema

```
learners/{uid}
  difficulty_tier, onboarding_complete

learners/{uid}/concepts/{lesson_id}
  { "0": {mastery_score, fsrs_stability, fsrs_difficulty, next_review_at, last_review_at},
    "1": { … } }   ← one entry per question index

learners/{uid}/sessions/{session_id}
  lesson_id, tier_used, quiz_scores, time_on_task_seconds,
  help_triggered (bool), gemini_handoff_used (bool),
  summary_text, created_at, fsrs_result
```

## Testing Requirements

- New backend logic: add a unit test in `backend/tests/` mocking Gemini SDK and Firestore
- New Flutter widgets: add a widget test in `app/test/widgets/`
- Integration tests in `backend/tests/integration/` — run against live Cloud Run before any deploy
- Integration tests require `CLOUD_RUN_URL` env var and ADC credentials — never run in CI without real GCP context

## Git Workflow

- Branch naming: `feature/`, `fix/`, `chore/` prefixes
- Never commit directly to `main`
- Commit messages: imperative mood, max 72 chars
- Never mention Claude/AI in commit messages
- Before any Cloud Run deploy: unit tests must pass (`pytest --ignore=tests/integration`)
