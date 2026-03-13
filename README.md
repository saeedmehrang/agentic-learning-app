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
# first run this:
 gcloud auth application-default login
# then run these
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

## Firebase Setup (Roadmap step 0.2)

**Prerequisites:** `firebase login` completed, GCP Bootstrap (step 0.1) done.

### 1. Link Firebase to the GCP project

> **Note:** The `firebase projects:addfirebase` CLI command consistently returns 403 PERMISSION_DENIED even with Owner + Firebase Admin roles, due to a Firebase API quota project mismatch. Use the console instead.

**Use the Firebase console (required):**
1. Go to [console.firebase.google.com](https://console.firebase.google.com)
2. Click **"Add project"** → select existing GCP project `agentic-learning-app`
3. Follow the wizard — enable Google Analytics, select **Europe** as the data location

After the console wizard completes, set the ADC quota project so CLI tools work correctly:
```bash
gcloud auth application-default set-quota-project agentic-learning-app
```

### 2. Enable Firebase APIs and Anonymous auth
```bash
./infra/scripts/enable_apis.sh   # now also enables Firestore, Identity Toolkit, Firebase APIs
```
Then enable the Anonymous sign-in provider:
```bash
ACCESS_TOKEN=$(gcloud auth print-access-token)
curl -s -X PATCH \
  "https://identitytoolkit.googleapis.com/admin/v2/projects/agentic-learning-app/config?updateMask=signIn.anonymous.enabled" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"signIn": {"anonymous": {"enabled": true}}}'
```

### 3. Enable Google Sign-In (manual — no CLI path)
Firebase console → Authentication → Sign-in method → Google → Enable → save.

### 4. Provision Firestore and update IAM
```bash
./infra/scripts/tf.sh plan
./infra/scripts/tf.sh apply
```
Adds `google_firestore_database` (Native mode, `us-central1`) and grants `roles/datastore.user` to the Cloud Run SA.

### 5. Enable Crashlytics (manual)
Firebase console → Crashlytics → Enable Crashlytics → accept terms.

### 6. Register apps and download config files
```bash
firebase apps:create android com.agenticlearning.app \
  --project=agentic-learning-app \
  --display-name="Agentic Learning App (Android)"

firebase apps:create ios com.agenticlearning.app \
  --project=agentic-learning-app \
  --display-name="Agentic Learning App (iOS)"

mkdir -p infra/firebase-config

ANDROID_APP_ID=$(firebase apps:list android --project=agentic-learning-app --json \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['result'][0]['appId'])")
firebase apps:sdkconfig android "$ANDROID_APP_ID" \
  --project=agentic-learning-app \
  --out=infra/firebase-config/google-services.json

IOS_APP_ID=$(firebase apps:list ios --project=agentic-learning-app --json \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['result'][0]['appId'])")
firebase apps:sdkconfig ios "$IOS_APP_ID" \
  --project=agentic-learning-app \
  --out=infra/firebase-config/GoogleService-Info.plist
```

Config files are stored in `infra/firebase-config/` (gitignored). They are placed into the Flutter project in Phase 5 via `flutterfire configure`.

> **Phase 5 follow-up:** Run `flutterfire configure` from `app/` after `flutter create` to place config files and generate `firebase_options.dart`.
