# Linux Learning App

Self-paced mobile learning platform for Linux basics. Bite-sized 7–10 minute sessions, spaced-repetition scheduling (FSRS), and short AI-assisted help conversations with a hard cap of 3 turns.

## Stack

| Layer | Technology |
|---|---|
| Mobile | Flutter (iOS + Android), Firebase Auth + Analytics |
| Backend | Google ADK agents on Cloud Run (scale-to-zero) |
| Knowledge store | Cloud SQL for PostgreSQL 17 + pgvector |
| Learner memory | Firestore |

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

## Phase Status

See [development_roadmap.md](development_roadmap.md) for the full checklist. Phase 0.1–0.3 complete.
