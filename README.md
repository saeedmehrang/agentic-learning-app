# Linux Learning App

Self-paced mobile learning platform for Linux basics. Bite-sized 7–10 minute sessions, spaced-repetition scheduling (FSRS), and short AI-assisted help conversations with a hard cap of 3 turns.

## Stack

| Layer | Technology |
|---|---|
| Mobile | Flutter (iOS + Android), Firebase Auth + Analytics |
| Backend | Google ADK agents on Cloud Run (scale-to-zero) |
| Knowledge store | Cloud SQL for PostgreSQL 17 + pgvector |
| Learner memory | Firestore |

## Claude Code Setup

Four Claude Code features are active in this project:

**Lint-on-save hooks** — [`.claude/settings.json`](.claude/settings.json)
Automatically runs `ruff check` after any Python file edit and `flutter analyze` after any Dart file edit. Warnings are printed inline but do not block the session.

**`/session` skill** — [`.claude/skills/session/SKILL.md`](.claude/skills/session/SKILL.md)
Run `/session` at the start of any work session. Reads `development_roadmap.md`, identifies the current phase and next 3 tasks, and outputs a focused brief with the relevant constraints and sub-agent to use.

**Sub-agent tool restrictions** — [`.claude/agents/`](.claude/agents/)
`character-agent` has no Bash access (image prompt and file work only). All other sub-agents (`backend-agent`, `flutter-agent`, `content-agent`, `infra-agent`) retain full tool access including Bash.

**GitHub MCP server** — [`.mcp.json`](.mcp.json)
Gives Claude access to GitHub APIs (issues, PRs, code search). Requires a GitHub token in your environment before launching Claude Code:
```bash
export GITHUB_TOKEN=$(gh auth token)   # if gh CLI is authenticated
# or set GITHUB_TOKEN to a PAT with repo scope from github.com/settings/tokens
```

## Python Version

Pinned in [`.python-version`](.python-version). When upgrading, update all four places:

| File | Field |
|---|---|
| `.python-version` | version number (source of truth) |
| `.devcontainer/Dockerfile` | `ARG PYTHON_VERSION=` |
| `.devcontainer/devcontainer.json` | `build.args.PYTHON_VERSION` |
| `backend/pyproject.toml` | `requires-python` and `ruff target-version` |

## Repository Layout

```
infra/
  terraform/    Terraform config — Cloud Run SA, Secret Manager, Firebase, Cloud SQL, VPC
  scripts/      Helper scripts — enable_apis.sh, tf.sh, push_secrets.sh, enable_pgvector.sh
backend/        Google ADK agents, FSRS scheduler, system prompts
content/        Lesson & quiz generation pipeline, approved content exports
assets/
  characters/   Character PNGs (characters × 6 emotions)
app/            Flutter source, Firebase config
```

## Key Design Constraints

- **≤ $12/month** at 100 active learners — every architectural choice is costed against this.
- **GCP-native only** — no third-party infra or self-hosted services.
- **Anonymous-first auth** — no sign-in wall; Google Sign-In offered after session 3.
- **HelpAgent hard cap** — 3 turns maximum; unresolved exits produce a `gemini_handoff_prompt`.
- **FSRS tool is LLM-free** — pure Python, no model calls.
- **Character assets bundled locally** — no runtime network image loading.

## Full Specification

See [learning_system_spec.md](learning_system_spec.md) for the complete technical spec.

---

## Infrastructure Bootstrap

> **Important — one GCP project only.** Firebase Console creates its own GCP project when you add Firebase to an existing GCP project. Use that Firebase-created project as the single project for everything. Do **not** create a separate GCP project manually and then try to link Firebase to it — this leads to two projects and quota/billing confusion. The project used here is `agentic-learning-app-e13cb`.

**Prerequisites:**
- `gcloud` CLI installed and authenticated (`gcloud auth login`)
- Terraform ≥ 1.5 installed
- Billing enabled on the GCP project (Blaze plan required for Cloud Run and Secret Manager)
- A Google OAuth 2.0 client ID and secret created in GCP Console → APIs & Services → Credentials

### 1. Configure environment

```bash
cp .env.example .env
```

Fill in `.env` — at minimum:
```
GCP_PROJECT_ID=your-firebase-created-project-id
GCP_REGION=us-central1
GOOGLE_OAUTH_CLIENT_ID=your-oauth-client-id
GOOGLE_OAUTH_CLIENT_SECRET=your-oauth-client-secret
```

All scripts and the Terraform wrapper (`tf.sh`) read `.env` automatically. No manual `export` needed.

### 2. Enable required GCP APIs

```bash
./infra/scripts/enable_apis.sh
```

Enables: Cloud Run, Cloud SQL Admin, Vertex AI, Secret Manager, Artifact Registry, Firestore, Identity Toolkit, Firebase, Crashlytics, Cloud Build, IAP, Compute Engine, Service Networking.

### 3. Import the Firebase project into Terraform state

Firebase Console creates the GCP project — Terraform must import it before it can manage it:

```bash
./infra/scripts/tf.sh import google_firebase_project.default projects/<your-project-id>
```

### 4. Provision all infrastructure

```bash
./infra/scripts/tf.sh init
./infra/scripts/tf.sh apply
```

A single `apply` provisions:
- Cloud Run service account with least-privilege IAM
- Secret Manager secrets (`DB_PASSWORD`, `DB_CONNECTION_NAME`, `GOOGLE_OAUTH_CLIENT_SECRET`)
- Firebase Android + iOS app registration
- `google-services.json` → `app/android/app/`
- `GoogleService-Info.plist` → `app/ios/Runner/`
- Firestore in Native mode (`us-central1`)
- Firebase Authentication with Identity Platform (Anonymous + Google Sign-In)
- Cloud SQL PostgreSQL 17, `db-f1-micro` Enterprise edition, private IP only
- VPC private service access peering (Cloud Run → Cloud SQL)

> Cloud SQL provisioning takes 10–15 minutes — this is normal.

### 5. Push DB_CONNECTION_NAME to Secret Manager

After apply, run:
```bash
./infra/scripts/push_secrets.sh
```

This reads the connection name from `terraform output` automatically and pushes it to Secret Manager.

### 6. Enable pgvector extension

```bash
./infra/scripts/enable_pgvector.sh
```

Starts the Cloud SQL Auth Proxy locally and runs `CREATE EXTENSION IF NOT EXISTS vector;` inside the `learning_app` database. Requires `cloud-sql-proxy` and `psql` installed.

### 7. Configure local dev credentials

```bash
gcloud auth application-default login
gcloud auth application-default set-quota-project <your-project-id>
```

Run once. All local SDK, Secret Manager, and Vertex AI calls use these credentials.

---

## Firebase Analytics & Crashlytics (manual step)

`firebaseanalytics.googleapis.com` cannot be enabled via CLI or Terraform. Enable it manually:

Firebase Console → your project → Analytics → Enable

---

---

## Observability

The backend emits OpenTelemetry spans to **Cloud Trace** and structured latency logs to **Cloud Logging**. Both work from local `uvicorn` runs and from Cloud Run.

### What is instrumented

| Signal | Where to view |
|---|---|
| Per-request waterfall (HTTP + all agent turns) | [Cloud Trace](https://console.cloud.google.com/traces/list?project=agentic-learning-app-e13cb) |
| Per-agent latency (`call_llm`, `execute_tool` sub-spans emitted by ADK) | Cloud Trace → expand any `agent_turn.*` span |
| `agent_turn_complete` structured log (latency_ms, agent, app_version) | [Cloud Logging](https://console.cloud.google.com/logs?project=agentic-learning-app-e13cb) → filter `jsonPayload.message="agent_turn_complete"` |
| Latency trends + before/after deployment comparison | Cloud Monitoring dashboard (see setup below) |

### One-time monitoring setup

Creates the log-based metrics and imports the latency dashboard into Cloud Monitoring:

```bash
bash infra/monitoring/setup_metrics.sh
```

Requires `roles/monitoring.admin` or project owner. Run once per GCP project.

### Deployment version tagging (before/after comparison)

Each Cloud Run deploy is tagged with the merge commit SHA so latency charts show a separate series per deployment:

```bash
APP_VERSION=$(git rev-parse --short HEAD)
gcloud run deploy backend \
  --set-env-vars APP_VERSION=$APP_VERSION \
  [... usual flags ...]
```

Convention: **squash-merge feature branches to `main`, then deploy immediately.** This makes each series in the Cloud Monitoring dashboard correspond to exactly one PR. Local runs always appear as `app_version=dev` and never pollute deployed metrics.

---

## Phase Status

See [development_roadmap.md](development_roadmap.md) for the full checklist. Phase 0.1–0.3 complete.
