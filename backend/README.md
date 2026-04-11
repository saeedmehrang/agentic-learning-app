# Backend — Google ADK Agent Service

FastAPI service hosting the 4-agent learning pipeline. Runs on Cloud Run (scale-to-zero) and locally via `uvicorn` for development and testing.

## Architecture

```
POST /session/start       → ContextAgent   (isolated session)
GET  /session/{id}/lesson → LessonAgent    (shared session with HelpAgent)
GET  /session/{id}/quiz/question
POST /session/{id}/quiz/answer → LessonAgent
POST /session/{id}/help   → HelpAgent      (max 3 turns, shared session with LessonAgent)
POST /session/{id}/complete → SummaryAgent (isolated session)
```

### Agents

| Agent | Model | Role |
|---|---|---|
| `ContextAgent` | Gemini 2.5 Flash | Reads Firestore learner memory + RAG search; picks next concept and difficulty tier |
| `LessonAgent` | Gemini 2.5 Flash | Delivers lesson, generates quiz questions, evaluates answers |
| `HelpAgent` | Gemini 2.5 Flash-Lite | Answers follow-up questions; 3-turn hard cap; produces `gemini_handoff_prompt` on unresolved exit |
| `SummaryAgent` | Gemini 2.5 Flash-Lite | Writes session record to Firestore; calls `run_fsrs` to update spaced-repetition schedule |

### Tools (plain async Python, no LLM)

| Tool | Purpose |
|---|---|
| `search_knowledge_base` | pgvector RAG search over lesson content (Cloud SQL) |
| `run_fsrs` | Pure-Python FSRS spaced-repetition scheduler; updates Firestore |
| `get_course_structure` | Returns course/module/lesson hierarchy from Cloud SQL |

## Local Development

### Prerequisites

- Python 3.13, `uv` installed
- `gcloud` authenticated + ADC configured:
  ```bash
  gcloud auth application-default login
  gcloud auth application-default set-quota-project agentic-learning-app-e13cb
  ```
- Cloud SQL Auth Proxy running (see [dev_chat/README.md](../dev_chat/README.md))

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
uv run pytest
```

### Lint + type check

```bash
uv run ruff check .
uv run mypy .
```

## Configuration

Settings are read from `.env` at the repo root (via `pydantic-settings`) and from Secret Manager at runtime. See [config.py](config.py) for all fields.

Key env vars:

| Variable | Default | Description |
|---|---|---|
| `GCP_PROJECT_ID` | `agentic-learning-app-e13cb` | GCP project |
| `GCP_LOCATION` | `us-central1` | Vertex AI region |
| `APP_ENV` | `development` | Environment label in logs |
| `APP_VERSION` | `dev` | Deployment version tag — set to `$(git rev-parse --short HEAD)` at deploy time |
| `DB_HOST` | `127.0.0.1` | Cloud SQL host (proxy) |
| `DB_PASSWORD` | _(Secret Manager)_ | Injected at runtime |

## Observability

The backend is instrumented with OpenTelemetry (Cloud Trace) and structured JSON logging.

### Cloud Trace

Every HTTP request produces a trace with child spans:
- `agent_turn.context_agent` / `lesson_agent` / `help_agent` / `summary_agent` — total time per agent call
- `call_llm` — Gemini API latency (emitted automatically by ADK)
- `execute_tool` — tool execution time (emitted automatically by ADK)

View traces: [Cloud Trace console](https://console.cloud.google.com/traces/list?project=agentic-learning-app-e13cb)

Traces are exported even from local `uvicorn` runs (requires ADC with `roles/cloudtrace.agent`).

### Structured logs

Every agent turn emits an `agent_turn_complete` log entry with:

```json
{
  "message": "agent_turn_complete",
  "agent": "lesson_agent",
  "latency_ms": 1843,
  "app_version": "a1b2c3d"
}
```

Filter in Cloud Logging: `jsonPayload.message="agent_turn_complete"`

### APP_VERSION and before/after comparison

`APP_VERSION` is injected at deploy time as the merge commit SHA:

```bash
gcloud run deploy backend --set-env-vars APP_VERSION=$(git rev-parse --short HEAD) ...
```

This tags all spans and log entries from a given Cloud Run revision with the version, enabling side-by-side latency comparison across deployments in the Cloud Monitoring dashboard. See `infra/monitoring/` for dashboard setup.

## Cloud Run Deploy

```bash
# Build image
gcloud builds submit --config infra/cloudbuild/backend.yaml .

# Deploy with version tag
APP_VERSION=$(git rev-parse --short HEAD)
gcloud run deploy backend \
  --image us-central1-docker.pkg.dev/agentic-learning-app-e13cb/agentic-learning/backend:$APP_VERSION \
  --region us-central1 \
  --service-account cloud-run-app-identity@agentic-learning-app-e13cb.iam.gserviceaccount.com \
  --set-env-vars APP_VERSION=$APP_VERSION
```
