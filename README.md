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
Copy `.env` and fill in your values (only `GCP_PROJECT_ID` and `GCP_REGION` need changing for a new project):
```bash
cp .env .env.local  # optional — .env already has sane defaults
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

### 3. Store secret values
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32), end='')" | \
  gcloud secrets versions add DB_PASSWORD --data-file=-
echo -n "agentic-learning-app:us-central1:learning-app-db" | \
  gcloud secrets versions add DB_CONNECTION_NAME --data-file=-
```
Secret values are never stored in code or `.env`. The backend config loader fetches them from Secret Manager at startup via Application Default Credentials.

### 4. Configure local dev credentials
```bash
gcloud auth application-default login
```
Run once. All local SDK and Secret Manager calls will use these credentials.
