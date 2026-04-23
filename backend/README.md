# Backend — Agentic Learning Service

FastAPI service running the lesson session pipeline. Deployed on Cloud Run (scale-to-zero). No ADK, no Cloud SQL, no pgvector — direct Gemini SDK only.

## Architecture

```
POST /session/start       → rate_limiter.check_rate_limit()
                          → scheduler.pick_next_lesson()     (pure Python, reads Firestore)
                          → LessonSession.__init__()          (stateful Gemini chat)

GET  /session/{id}/lesson         → LessonSession.teach()
GET  /session/{id}/quiz/question  → LessonSession.next_question()
POST /session/{id}/quiz/answer    → LessonSession.evaluate_answer()
POST /session/{id}/help           → HelpSession.respond()    (max 3 turns, hard-capped)
POST /session/{id}/complete       → summary_call.run_summary() + run_fsrs() + Firestore write
```

### Components

| Module | Type | Model | Role |
|---|---|---|---|
| `scheduler.py` | Pure Python | None | Read Firestore FSRS schedule; pick next lesson + tier |
| `lesson_session.py` — `LessonSession` | `client.chats.create()` | Gemini 3.1 Flash-Lite, thinking=LOW (1024) | Deliver lesson; run quiz loop; emit `trigger_help` on 2nd consecutive wrong answer |
| `lesson_session.py` — `HelpSession` | `client.chats.create()` | Gemini 3.1 Flash-Lite, thinking=MINIMAL (0) | 3-turn clarification; outputs `gemini_handoff_prompt` on unresolved exit |
| `summary_call.py` | `generate_content()` | Gemini 3.1 Flash-Lite, thinking=MINIMAL (0) | Summarise session; call `run_fsrs()`; write Firestore |
| `rate_limiter.py` | Pure Python | None | Per-UID session-start rate limit (10/hour) via Firestore transaction |
| `run_fsrs.py` | Pure Python | None | FSRS-4 spaced-repetition algorithm — deterministic, no LLM |
| `cache_manager.py` | Pure Python | None | Gemini context cache builder (disabled by default) |

All `genai.Client` calls use `genai.Client(vertexai=True, location="global")` — required for `gemini-3.1-flash-lite-preview` which is only available via the global Vertex AI endpoint.

## Local Development

### Prerequisites

- Python 3.13, `uv` installed
- `gcloud` authenticated with ADC:
  ```bash
  gcloud auth application-default login
  gcloud auth application-default set-quota-project agentic-learning-app-e13cb
  ```

### Install dependencies

```bash
cd backend
uv sync
```

### Run the server

```bash
uv run uvicorn main:app --host 0.0.0.0 --port 8080 --reload
```

Health check: `curl http://localhost:8080/health`

### Run tests

```bash
# Unit tests (no network, no GCP)
uv run pytest --ignore=tests/integration

# Integration tests (requires live Cloud Run + ADC)
CLOUD_RUN_URL=https://backend-1081017476491.us-central1.run.app \
  uv run pytest tests/integration/test_session_e2e.py -v

# Load tests (concurrent + response time baseline)
CLOUD_RUN_URL=https://backend-1081017476491.us-central1.run.app \
  uv run pytest tests/integration/test_load.py -v
```

### Lint

```bash
uv run ruff check .
```

## Configuration

Settings are loaded from `.env` at the repo root and from Secret Manager at runtime. See [config.py](config.py) for all fields.

Key env vars:

| Variable | Default | Description |
|---|---|---|
| `GCP_PROJECT_ID` | `agentic-learning-app-e13cb` | GCP project |
| `GCP_LOCATION` | `us-central1` | Cloud Run region |
| `GCS_PIPELINE_BUCKET` | _(empty)_ | GCS bucket for approved lesson JSON files; empty = local filesystem (dev/tests) |
| `APP_ENV` | `development` | Environment label in logs |
| `APP_VERSION` | `dev` | Deployment version — set to `$(git rev-parse --short HEAD)` at deploy time |
| `ENABLE_LESSON_CACHE` | `false` | Set to `true` to enable Gemini context caching |
| `MAX_SESSIONS_PER_HOUR` | `10` | Per-UID session-start rate limit |

## Rate Limiting

`POST /session/start` enforces a per-UID limit of `MAX_SESSIONS_PER_HOUR` (default 10) session starts per rolling 60-minute window. Implemented via a Firestore transaction in `rate_limiter.py`. Returns HTTP 429 with a `Retry-After` header when exceeded.

Note: the `uid` field is currently self-reported (no Firebase token verification). Firebase ID token verification is planned for Phase 6.5 before public launch.

## Firestore Schema

```
learners/{uid}
  difficulty_tier, onboarding_complete

learners/{uid}/concepts/{lesson_id}
  { "0": {mastery_score, fsrs_stability, fsrs_difficulty, next_review_at, last_review_at}, … }

learners/{uid}/sessions/{session_id}
  lesson_id, tier_used, quiz_scores, time_on_task_seconds,
  help_triggered, gemini_handoff_used,
  summary_text, created_at, fsrs_result

rate_limits/{uid}
  count, window_start   ← rate limiter rolling window
```

## Observability

Instrumented with OpenTelemetry (Cloud Trace) and structured JSON logging.

Every HTTP request produces a trace. View traces in the [Cloud Trace console](https://console.cloud.google.com/traces/list?project=agentic-learning-app-e13cb).

`APP_VERSION` is injected at deploy time as the commit SHA, tagging all spans and log entries for side-by-side latency comparison across deployments. See `infra/monitoring/` for the Cloud Monitoring dashboard.

## Cloud Run Deploy

```bash
# Build and push image
gcloud builds submit --config infra/cloudbuild/backend.yaml \
  --substitutions=COMMIT_SHA=$(git rev-parse --short HEAD) .

# Deploy
gcloud run deploy backend \
  --image us-central1-docker.pkg.dev/agentic-learning-app-e13cb/agentic-learning/backend:latest \
  --region us-central1 --platform managed --allow-unauthenticated \
  --min-instances 0 \
  --service-account cloud-run-app-identity@agentic-learning-app-e13cb.iam.gserviceaccount.com \
  --set-env-vars APP_ENV=production,GCP_PROJECT_ID=agentic-learning-app-e13cb,GCP_LOCATION=us-central1,GCS_PIPELINE_BUCKET=agentic-learning-pipeline,ENABLE_LESSON_CACHE=false,APP_VERSION=$(git rev-parse --short HEAD)
```

Full deploy reference: `notes/phase4-deployment-instructions.md`
