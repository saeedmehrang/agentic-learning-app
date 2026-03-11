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

## GCP Bootstrap (Roadmap step 0.1)

**Prerequisites:** `gcloud` CLI authenticated (`gcloud auth login`), Terraform ≥ 1.5 installed, billing enabled on the GCP project.

### 0. Configure environment
Copy `.env.example` and fill in your values (only `GCP_PROJECT_ID` and `GCP_REGION` need changing for a new project):
```bash
cp .env.example .env  # .env already has sane defaults
```
All scripts and the Terraform wrapper read `.env` automatically. No manual `export` needed.

### 1. Enable required APIs
```bash
./infra/scripts/enable_apis.sh
```

### 2. Provision service account and Secret Manager containers
```bash
./infra/scripts/tf.sh init
./infra/scripts/tf.sh apply
```
`tf.sh` is a thin wrapper that injects `.env` values as Terraform variables (`GCP_PROJECT_ID` → `project_id`, `GCP_REGION` → `region`) before delegating to `terraform`.

This creates the Cloud Run service account with least-privilege IAM roles (`cloudsql.client`, `aiplatform.user`) and the Secret Manager secret containers (`DB_PASSWORD`, `DB_CONNECTION_NAME`).

### 3. Push secret values to Secret Manager

```bash
./infra/scripts/push_secrets.sh
```

The script is designed so secret values **never touch disk**:

- **`DB_PASSWORD`** — generated from `/dev/urandom` inside the script and piped directly to Secret Manager via stdin. You never see or store it. The app reads it from Secret Manager at runtime.
- **`DB_CONNECTION_NAME`** — prompted interactively with hidden input (`read -rs`). Lives only in a shell variable for the duration of the script, then discarded.

The script skips any secret that already has an enabled version (idempotent) and skips `DB_CONNECTION_NAME` if you leave the prompt blank.

> **Phase 0.3 follow-up:** Re-run `push_secrets.sh` after Cloud SQL is provisioned — enter the connection name (`project:region:instance`) at the prompt.

Secret values are never stored in `.env`, code, or shell history. The backend fetches them from Secret Manager at startup via Application Default Credentials.

### 4. Configure local dev credentials
```bash
gcloud auth application-default login
```
Run once. All local SDK and Secret Manager calls will use these credentials.
