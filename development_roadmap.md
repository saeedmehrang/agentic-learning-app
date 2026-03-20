# 🗺️ Development Roadmap
## Agentic Self-Paced Learning System — MVP v1.0

---

## 📊 Phase Progress

| Phase | Title | Status |
|---|---|---|
| Phase 0 | Project Setup & GCP Infrastructure | ☐ |
| Phase 1 | Content & Knowledge Base | ☐ |
| Phase 2 | Character Asset Production | ☐ |
| Phase 3 | Backend Scaffold & Agent Foundation | ☐ |
| Phase 4 | Core Agent Pipeline | ☐ |
| Phase 5 | Flutter App | ☐ |
| Phase 6 | Trial Launch & Iteration | ☐ |

> Replace `☐` with `✅` when a phase is complete.

---

## Development Environment

devcontainer isolated: A devcontainer gives you reproducible Python (ADK), Dart/Flutter, and Node (content pipeline scripts) environments in one place, with GCP CLI and Firebase CLI pre-installed.

Local system wide installation: Flutter for mobile needs Android SDK and iOS tools, which devcontainers can't fully provide — iOS tooling requires a native macOS host, and Android emulation inside a container is painful.

Practical split:

- Use the devcontainer for everything backend — ADK agents, content pipeline, infra scripts, Cloud SQL migrations. This is where isolation matters most.
- Run Flutter natively on your host machine, connected to the containerised backend via localhost port-forwarding.

---

## Phase 0 — Project Setup & GCP Infrastructure

> **Goal:** All cloud services provisioned, IAM configured, local dev environment ready. Nothing can be built until this is done.

### 0.1 GCP Project Bootstrap
- [x] Create GCP project (`agentic-learning-app` or equivalent)
- [x] Enable billing and set a monthly budget alert (e.g. $25/month)
- [x] Enable required APIs: Cloud Run, Cloud SQL Admin, Vertex AI, Secret Manager, Artifact Registry
- [x] Create a dedicated service account for Cloud Run with least-privilege IAM roles
- [x] Store all secrets (DB credentials, API keys) in Secret Manager — no plaintext credentials in code
- [x] Set `DB_PASSWORD` in `.env` and run `./infra/scripts/push_secrets.sh` to push it to Secret Manager (run immediately after `terraform apply`)

### 0.2 Firebase Project Setup
- [x] Create Firebase project via Firebase Console — Firebase creates its own GCP project (`agentic-learning-app-e13cb`); use this as the single GCP project for everything (do NOT create a separate GCP project and link — causes two-project confusion). CLI `projects:addfirebase` returns 403 regardless of IAM; console is the only path.
- [x] Terraform for Firebase written (`infra/terraform/firebase.tf`): enables Firebase/Crashlytics APIs, imports Firebase project, registers Android and iOS apps, writes `google-services.json` / `GoogleService-Info.plist` to Flutter app directories
- [x] Run `terraform import google_firebase_project.default projects/agentic-learning-app-e13cb` then `terraform apply` — provisions Firebase apps and downloads config files
- [x] Enable Firebase Authentication (Anonymous provider + Google Sign-In) — via Identity Platform (`auth.tf`); OAuth client secret stored in Secret Manager
- [x] Enable Firestore in Native mode (us-central1) — provisioned via Terraform
- [ ] Enable Firebase Analytics and Crashlytics — `firebaseanalytics.googleapis.com` cannot be enabled via CLI/Terraform; must be done via Firebase Console
- [x] Verify `google-services.json` and `GoogleService-Info.plist` are written to `app/android/app/` and `app/ios/Runner/` respectively after apply

### 0.3 Cloud SQL Setup
- [x] Provision Cloud SQL for PostgreSQL 17 instance (`db-f1-micro` Enterprise edition, `us-central1`) — provisioned via `infra/terraform/cloudsql.tf`
- [ ] Enable the `pgvector` extension on the database — run `./infra/scripts/enable_pgvector.sh` (requires `cloud-sql-proxy` + `psql` installed locally)
- [x] Create application database (`learning_app`) and dedicated DB user (`app_user`) with password from Secret Manager — Terraform managed
- [x] Configure private IP (VPC) access between Cloud SQL and Cloud Run — VPC peering provisioned via Terraform (`google_compute_global_address`, `google_service_networking_connection`)
- [ ] Test connection from local machine via Cloud SQL Auth Proxy
- [x] Set `DB_CONNECTION_NAME` in Secret Manager — auto-pushed by `push_secrets.sh` reading `terraform output cloud_sql_connection_name`

### 0.4 Local Development Environment
- [x] Install and configure Google Cloud SDK (`gcloud` CLI)
- [x] Install ADK CLI and Python dependencies (`google-adk`, `psycopg2-binary`, `google-cloud-firestore`) — baked into devcontainer Dockerfile; `backend/pyproject.toml` locks versions via `uv`
- [ ] Install Flutter SDK and set up iOS Simulator + Android Emulator — runs natively on host machine, not in devcontainer (see Dev Environment note above)
- [x] Create a `.env` file template with all required env vars (committed as `.env.example`, real file gitignored)
- [x] Set up Git repository with branch protection on `main`

---

## Phase 1 — Content & Knowledge Base

> **Goal:** All 29 lessons written, tiered, embedded, and loaded into Cloud SQL. The knowledge base must be complete before agents can be meaningfully tested end-to-end.

### 1.1 Course Structure & Lesson Outlines
- [x] Finalise the course structure for all 9 modules (29 lessons including the Shell Scripting module)
- [x] Write a lesson outline for each of the 29 lessons: learning objectives, key concepts, example commands/scenarios — committed to `content/outlines/linux_basics_outlines.yaml`
- [x] Define the prerequisite graph: `prerequisites[]` array per lesson — all prerequisite edges mapped in the outlines YAML
- [ ] Assign `concept_tags[]` per lesson (these drive FSRS mastery tracking per concept, not per lesson)

### 1.2 Course Concept Map *(required for every new course — do this before generation)*

> **What it is:** A JSON file (`content/{course_id}_concept_map.json`) that gives the content generation pipeline a bird's-eye view of the whole course. Without it, Gemini writes each lesson in isolation and may re-explain already-covered concepts, introduce concepts ahead of schedule, or miss cross-lesson connections.
>
> **What it contains:**
> - `lessons`: for each lesson — which concepts it **introduces** (first time a concept is taught) vs which it **assumes** (concepts introduced in earlier lessons that can be referenced without re-teaching)
> - `concept_index`: a reverse map from concept name → the lesson that first introduces it — used by the generation script to resolve prerequisite context without passing the full outline
> - `course_metadata`: total lesson count, module structure, and tier definitions
>
> **How it is generated:** Run Claude Code (Sonnet) over the completed outlines YAML once, before any Gemini generation calls. The output is committed to the repo alongside the outlines and treated as a stable artifact — only regenerate if the outlines change significantly. See `content/linux_basics_concept_map.json` as the reference example.
>
> **Why this matters for quality:** The generation script uses the concept map to build a compact per-lesson context (~40 lines) — the target lesson block plus one-line summaries of prerequisite concepts. This prevents Gemini's context window from being filled with irrelevant lessons while still giving it the cross-lesson awareness needed to write coherent, non-repetitive content.

- [x] Generate `courses/linux-basics/concept_map.json` using Claude Code (Sonnet) from the completed outlines YAML
- [x] Human review: confirm concept introduction order is correct and no concept is listed as assumed before it is introduced
- [ ] **For every future course:** repeat this step — generate `courses/{course_id}/concept_map.json` before running any content generation

### 1.3 Content Generation Pipeline
- [x] Write prompt templates for lesson and quiz generation — committed to `courses/linux-basics/prompts/`
  - `courses/linux-basics/prompts/lesson_generation.md` — 3-tier lesson prompt with `terminal_steps` schema
  - `courses/linux-basics/prompts/quiz_generation.md` — 4-format quiz prompt, all tap-to-select
- [x] Write `content-generation/generate_content.py`:
  - Reads `courses/linux-basics/outlines.yaml` and `courses/linux-basics/concept_map.json`
  - Builds a compact per-lesson context (lesson block + prereq concept summaries from the map)
  - Single Gemini call per lesson × tier produces both lesson content and quiz questions
  - Default quiz parameters: 8 questions per lesson per tier, all 4 formats
  - Writes structured JSON output to `courses/linux-basics/pipeline/generated/`
  - Supports `--lesson`, `--tier`, `--dry-run`, `--resume` flags; bounded async concurrency (default 5)
- [ ] ⚙️ **OPERATIONAL**: Run content generation — `python content-generation/generate_content.py --resume`
  - 87 total API calls (29 lessons × 3 tiers), model = `gemini-2.0-flash`
  - Output: `courses/linux-basics/pipeline/generated/L##_[tier].json` (gitignored)
- [ ] **OPERATIONAL**: Human review — read all generated files, move approved ones to `courses/linux-basics/pipeline/approved/`; flag any for regeneration
- [x] Write `content-generation/embed_content.py`:
  - Reads approved JSON from `courses/linux-basics/pipeline/approved/`
  - Calls Vertex AI `text-embedding-005` (768-dim, `RETRIEVAL_DOCUMENT` task type)
  - Writes embedded JSON (text + vector + quiz pass-through) to `courses/linux-basics/pipeline/embedded/`
  - Supports `--lesson`, `--tier`, `--dry-run`, `--resume` flags; bounded async concurrency (default 5)
- [ ] ⚙️ **OPERATIONAL**: Run embedding — `python content-generation/embed_content.py --resume`

### 1.4 Quiz Question Generation
> Quiz generation is integrated into `generate_content.py` (see 1.3) — not a separate script. Each Gemini call produces both the lesson content and the quiz questions for that lesson × tier in a single pass.

- [ ] **OPERATIONAL**: Human review — validate all quiz questions for correctness and appropriate difficulty per tier
- [ ] **OPERATIONAL**: Ensure every question has `answer`, `options[]` (where applicable), and `explanation` fields populated — no `accept_variants` or free-text input fields (all formats are tap-to-select)

### 1.5 Database Loading

> **Note:** VPC connector is NOT required for the seed job. `seed_db.py` uses the Cloud SQL Python Connector (TLS tunnel via Admin API), which works from Cloud Run Jobs without private IP access. The old memory file `project_phase14_db_seeding.md` is superseded by the steps below.

- [x] Write `infra/sql/001_create_schema.sql` — idempotent DDL for `lessons`, `content_chunks` (ivfflat pgvector index), `quiz_questions`
- [x] Write `infra/scripts/apply_schema.sh` — applies DDL via `gcloud sql connect` (same pattern as `enable_pgvector.sh`)
- [x] Write `content-generation/seed_db.py` — Cloud Run Job script; reads embedded JSON, bulk-inserts into Cloud SQL via Cloud SQL Python Connector; all inserts idempotent
- [x] Write `content-generation/Dockerfile.seed` — image for the Cloud Run Job (build context = repo root)
- [x] Write `infra/terraform/artifact_registry.tf` — provisions Docker image registry; grants Cloud Run SA reader access
- [ ] ⚙️ **OPERATIONAL**: Provision Artifact Registry — `terraform apply` in `infra/terraform/`
- [ ] ⚙️ **OPERATIONAL**: Enable pgvector — `./infra/scripts/enable_pgvector.sh` (one-time)
- [ ] ⚙️ **OPERATIONAL**: Apply schema — `./infra/scripts/apply_schema.sh` (one-time)
- [ ] ⚙️ **OPERATIONAL**: Build and push seed image:
  ```
  IMAGE=us-central1-docker.pkg.dev/agentic-learning-app-e13cb/agentic-learning/content-seed:latest
  gcloud builds submit --tag "$IMAGE" --dockerfile content-generation/Dockerfile.seed .
  ```
- [ ] ⚙️ **OPERATIONAL**: Create Cloud Run Job (one-time):
  ```
  gcloud run jobs create content-seed --image "$IMAGE" --region us-central1 \
    --service-account cloud-run-app-identity@agentic-learning-app-e13cb.iam.gserviceaccount.com \
    --set-secrets DB_PASSWORD=DB_PASSWORD:latest,DB_INSTANCE_CONNECTION_NAME=DB_CONNECTION_NAME:latest \
    --memory 512Mi --max-retries 1
  ```
- [ ] ⚙️ **OPERATIONAL**: Execute seed job — `gcloud run jobs execute content-seed --region us-central1 --wait`
- [ ] ⚙️ **OPERATIONAL**: Verify data via `gcloud sql connect`:
  - `SELECT COUNT(*) FROM lessons;` — expect 29
  - `SELECT COUNT(*) FROM content_chunks;` — expect 87
  - `SELECT COUNT(*) FROM quiz_questions;` — expect 696 (87 × 8)
  - Run a test cosine similarity query to confirm pgvector search works
- [ ] Seed the 9 module character assignment records (`module_id` → `character_id` mapping) — deferred to Phase 2 when characters are finalised

---

## Phase 2 — Character Asset Production

> **Goal:** All 9 module characters (8 original + 1 for Shell Scripting) are designed, generated, reviewed, optimised, and ready to bundle into the app. This phase runs in parallel with Phase 1 and must complete before Phase 5.

### 2.1 Style Guide & Character Definitions
- [ ] Define the new character for Module 9 (Shell Scripting) — name, visual concept, colour palette
  - Suggested: *Scrippy* — a small scroll/parchment character with a quill pen
- [ ] Write a style anchor prompt for each of the 9 characters following the template in spec §4.3
- [ ] Document all 9 style anchors in `assets/characters/style_anchors.md` — single source of truth for all future regenerations

### 2.2 Asset Generation (Validate First, Then Scale)
- [ ] Generate all 6 emotion variants for **Cursor** (Module 2) first as the validation character
- [ ] Review Cursor's 6 images side-by-side: colour consistency, proportions, line weight, transparent background
- [ ] Iterate on any inconsistent Cursor variants (up to 3 regeneration attempts per image)
- [ ] Once Cursor is approved, generate all 6 emotions for **Tux Jr.** (Module 1) and **Filo** (Module 3)
- [ ] Review Modules 1–3 characters — do not proceed to remaining characters until these are approved
- [ ] Generate all 6 emotions for remaining 6 characters: Snippy, Keyra, Spinner, Wavo, Boxby, and the Module 9 character
- [ ] Final review pass: all 9 characters × 6 emotions = 54 PNGs reviewed and approved

### 2.3 Asset Export & Optimisation
- [ ] Export all approved images as PNG with transparent background
- [ ] Name all files consistently: `{character_id}_{emotion}.png` (e.g. `cursor_celebrating.png`)
- [ ] Run `pngquant` or `optipng` on all 54 PNGs (target: under 80 KB each, total bundle under 4.5 MB)
- [ ] Place all optimised PNGs in `assets/characters/` directory in the Flutter project
- [ ] Declare all 54 assets in `pubspec.yaml` under `flutter: assets:`

---

## Phase 3 — Backend Scaffold & Agent Foundation

> **Goal:** A running Cloud Run service with the ADK pipeline skeleton wired to live GCP data sources. The `search_knowledge_base` tool and ContextAgent are working end-to-end. This is the foundation all other agents build on.

### 3.1 ADK Project Scaffold
- [ ] Initialise ADK project structure with `adk init`
- [ ] Define the top-level sequential agent pipeline: `ContextAgent → LessonAgent → HelpAgent (conditional) → SummaryAgent`
- [ ] Set up `pyproject.toml` / `requirements.txt` with all ADK and GCP library dependencies
- [ ] Write a local `.env` config loader that reads from Secret Manager in production and from `.env` in dev
- [ ] Set up structured logging (JSON format) for Cloud Run compatibility

### 3.2 `search_knowledge_base` Tool
- [ ] Implement `search_knowledge_base(concept_id, tier)` as a standalone Python tool (not an agent)
- [ ] Tool logic: embed the concept query via Vertex AI `text-embedding-004`, run pgvector cosine similarity search, return top-3 content chunks filtered by difficulty tier
- [ ] Wire Cloud SQL connection via Cloud SQL Auth Proxy (local) and private IP (Cloud Run)
- [ ] Unit test: verify the correct chunks are returned for several concept queries at each of the 3 difficulty tiers

### 3.3 ContextAgent
- [ ] Implement ContextAgent as an `LlmAgent` (Gemini 2.5 Flash-Lite)
- [ ] Wire Firestore read: fetch learner profile, FSRS-scheduled next concept(s), difficulty tier, and last session summary
- [ ] Implement logic to determine which module character to assign based on the scheduled concept's module
- [ ] Output structured JSON: `{ next_concept_id, difficulty_tier, module_character_id, session_goal }`
- [ ] Unit test: mock Firestore reads, verify correct concept and character selection logic for several learner states (new learner, returning learner, struggling learner)

### 3.4 Cloud Run Deployment (Skeleton)
- [ ] Write `Dockerfile` for the ADK service
- [ ] Set up Cloud Build trigger: push to `main` → build and push Docker image to Artifact Registry
- [ ] Deploy skeleton service to Cloud Run (scale-to-zero, minimum instances = 0)
- [ ] Configure Cloud Run environment variables via Secret Manager references
- [ ] Verify deployed service responds to a health-check endpoint
- [ ] Set up VPC connector so Cloud Run can reach Cloud SQL via private IP

---

## Phase 4 — Core Agent Pipeline

> **Goal:** All 4 agents fully implemented, tools wired, and the complete session pipeline tested end-to-end via direct API call to Cloud Run. The Flutter app is not required for this phase.

### 4.1 `run_fsrs` Tool
- [ ] Implement `run_fsrs(concept_id, mastery_score, outcome)` as a deterministic Python tool (no LLM)
- [ ] Inputs: current `fsrs_stability`, `fsrs_difficulty`, `mastery_score`, quiz outcome (correct / incorrect)
- [ ] Outputs: updated `fsrs_stability`, `fsrs_difficulty`, `next_review_at` timestamp per concept
- [ ] Unit test FSRS scheduling: verify review intervals lengthen for well-mastered concepts and shorten for struggling ones
- [ ] Verify the tool is importable and callable standalone before wiring into SummaryAgent

### 4.2 LessonAgent
- [ ] Implement LessonAgent as a tool-calling `LlmAgent` (Gemini 2.0 Flash) with `search_knowledge_base` registered as its tool
- [ ] Write the LessonAgent system prompt covering both the teaching phase and quiz phase — include character personality instructions keyed on `module_character_id`
- [ ] **Teaching phase:** agent calls `search_knowledge_base`, generates lesson narrative from returned chunks, outputs `{ lesson_text, character_emotion_state: "teaching", key_concepts[] }`
- [ ] **Quiz phase (multi-turn):** agent generates one question at a time in one of the 4 formats (mc / tf / fill / command), outputs `{ question_text, format, options[], character_emotion_state: "curious" }`
- [ ] **Answer evaluation:** agent receives learner answer with full lesson context still in window, outputs `{ correct: bool, explanation, concept_score_delta, character_emotion_state: "celebrating"|"encouraging" }`
- [ ] **Help trigger:** on 2nd consecutive wrong answer for the same concept, agent outputs `{ trigger_help: true }` and pauses — control passes to HelpAgent
- [ ] Validate that `character_emotion_state` is always one of the 6 defined states in every output
- [ ] Test all 9 module characters: confirm character voice and tone adapts per character personality
- [ ] Unit test all 4 quiz formats with both correct and incorrect learner answers
- [ ] Unit test help trigger: verify `trigger_help: true` fires on exactly the 2nd wrong answer, not the 1st

### 4.3 HelpAgent
- [ ] Implement HelpAgent as a 3-turn stateful `LlmAgent` (Gemini 2.0 Flash)
- [ ] Implement the HelpAgent state machine: `IDLE → ACTIVE → RESOLVED` (see spec §3.4)
- [ ] Write HelpAgent system prompt with hard 3-turn constraint (see spec §3.4 constraint block)
- [ ] On resolved exit (any turn): output `{ resolved: true, character_emotion_state: "celebrating" }` — control returns to LessonAgent
- [ ] On unresolved exit at turn 3: output `{ resolved: false, gemini_handoff_prompt: "..." }` — pipeline surfaces the Gemini referral card
- [ ] Test all resolution paths: resolved at turn 1, resolved at turn 2, resolved at turn 3, unresolved at turn 3
- [ ] Adversarial test: attempt to coax the agent past 3 turns with ambiguous learner answers — confirm hard cap holds under all variations

### 4.4 SummaryAgent
- [ ] Implement SummaryAgent as a tool-calling `LlmAgent` (Gemini 2.5 Flash-Lite) with `run_fsrs` registered as its tool
- [ ] Agent calls `run_fsrs` for each concept touched in the session, then writes all outputs to Firestore
- [ ] Write session record to Firestore: `{ lesson_id, tier_used, quiz_scores{}, time_on_task_seconds, help_triggered, gemini_handoff_used, summary_text, created_at }`
- [ ] Write updated concept mastery to Firestore: `{ mastery_score, fsrs_stability, fsrs_difficulty, last_review_at, next_review_at, review_count }` per concept
- [ ] Unit test: mock quiz outcomes, verify Firestore writes contain all required fields with correct types and that FSRS-computed `next_review_at` values are reasonable

### 4.5 End-to-End Pipeline Integration
- [ ] Wire the full pipeline: `ContextAgent → LessonAgent → HelpAgent (conditional) → SummaryAgent`
- [ ] Implement the pipeline router: LessonAgent's `trigger_help: true` output activates HelpAgent; control returns to LessonAgent after HelpAgent resolves
- [ ] Write an integration test script simulating three complete session scenarios via direct API call to Cloud Run:
  - Happy path: learner answers all questions correctly, no help triggered
  - Help path (resolved): learner answers incorrectly twice, HelpAgent resolves at turn 2
  - Help path (unresolved): learner answers incorrectly twice, HelpAgent exits unresolved, `gemini_handoff_prompt` is present in response
- [ ] Verify all Firestore state after each test: mastery scores updated, session record created, `next_review_at` written per concept
- [ ] Load test: simulate 10 concurrent sessions against Cloud Run — confirm scale-to-zero behaviour and measure cold start latency

---

## Phase 5 — Flutter App

> **Goal:** A fully working Flutter app connected to the deployed Cloud Run backend. All screens implemented. Full session flow tested on real devices. Internal testing complete.

### 5.1 App Skeleton & Auth
- [ ] Create Flutter project with correct bundle IDs for iOS and Android
- [ ] Add Firebase to the project: place `google-services.json` and `GoogleService-Info.plist`, configure `firebase_options.dart`
- [ ] Implement Firebase Anonymous Authentication on first launch
- [ ] Set up Riverpod as the state management solution — create providers for auth state, session state, and learner profile
- [ ] Set up Hive for local session cache (session state survives app backgrounding)
- [ ] Set up `http` package with a base API client pointing to the Cloud Run endpoint (with Firebase auth token injection)
- [ ] Register all 6 required Firebase Analytics events: `session_start`, `lesson_complete`, `quiz_answer`, `help_triggered`, `gemini_handoff_tapped`, `session_complete`
- [ ] Set up Firebase Crashlytics

### 5.2 Character Widget
- [ ] Build `CharacterWidget` as a reusable Flutter widget
- [ ] Implement `AnimatedCrossFade` (300ms) between emotion PNG assets, driven by `emotion_state` string from any agent response
- [ ] Implement character asset loader: given `module_character_id` + `emotion_state`, load the correct PNG from bundled assets
- [ ] Implement size/position states: corner mode (80×80dp, top-right) and help mode (120×120dp, centred)
- [ ] Test all 9 characters × 6 emotions — verify no missing assets and no layout overflow on both iOS and Android

### 5.3 Onboarding & Splash Screen
- [ ] Build Splash Screen: Firebase anonymous sign-in fires here — show app logo and loading indicator
- [ ] Build Onboarding Screen (first-launch only): 3-question difficulty tier assignment quiz
- [ ] Wire onboarding answers to Firestore learner profile creation (`difficulty_tier`, `onboarding_complete: true`)
- [ ] Implement first-launch detection: if `onboarding_complete` is false, route to Onboarding; otherwise route to Home

### 5.4 Home / Dashboard Screen
- [ ] Build Home Screen layout: progress overview, next lesson card with module character thumbnail, streak counter
- [ ] Fetch learner's next scheduled lesson from Firestore and display module character thumbnail and lesson title on the next lesson card
- [ ] Implement streak counter (sessions completed on consecutive calendar days)
- [ ] Build Google Sign-In upgrade prompt: non-blocking bottom sheet, shown once after session 3 completes for the first time
- [ ] Wire Google Sign-In: link anonymous UID to Google account, preserve all history, confirm UID unchanged post-link

### 5.5 Session Screen — Lesson Phase
- [ ] Build Session Screen: `CharacterWidget` in top-right corner (80×80dp), lesson text card, progress bar at top
- [ ] Call Cloud Run session start endpoint — send `learner_uid`, receive ContextAgent + LessonAgent teaching phase response
- [ ] Display lesson text from LessonAgent response; drive `CharacterWidget` emotion from `character_emotion_state` field
- [ ] Handle loading state: skeleton loader while waiting for the agent pipeline response
- [ ] On lesson text received, show a "Ready to be quizzed?" CTA to advance to quiz phase

### 5.6 Session Screen — Quiz Phase
- [ ] Build quiz question card component shared across all 4 question formats
- [ ] Implement Multiple Choice format: 4-option button list
- [ ] Implement True/False format: two large toggle buttons
- [ ] Implement Fill-in-the-blank format: text input with submit button
- [ ] Implement Command Completion format: monospace text input with syntax hint
- [ ] Submit learner answer to Cloud Run LessonAgent evaluation endpoint; receive `{ correct, explanation, concept_score_delta, character_emotion_state }`
- [ ] Drive `CharacterWidget` emotion from evaluation response (`celebrating` on correct, `encouraging` on first wrong answer)
- [ ] On receiving `trigger_help: true` in LessonAgent response: activate the Help Bottom Sheet

### 5.7 Help Bottom Sheet & Gemini Referral Card
- [ ] Build Help Bottom Sheet: slides up as bottom sheet when `trigger_help: true` is received
- [ ] During help turns: `CharacterWidget` animates to centre (120×120dp) and switches to `helping` emotion state
- [ ] Render each HelpAgent turn exchange as a conversation bubble (agent message + learner reply input)
- [ ] Implement "Got it" button: dismisses bottom sheet, returns `CharacterWidget` to corner, resumes quiz phase
- [ ] On receiving `resolved: false` + `gemini_handoff_prompt` after turn 3: dismiss bottom sheet and display Gemini Referral Card
- [ ] Build Gemini Referral Card: dismissible overlay card with "Still stuck? Keep learning this in Gemini →" CTA
- [ ] Implement `url_launcher` deep-link: tap opens Gemini app (or `gemini.google.com`) with `gemini_handoff_prompt` pre-filled
- [ ] Log `gemini_handoff_tapped` Firebase Analytics event on tap

### 5.8 Session Complete Screen
- [ ] Build Session Complete Screen: summary text card, `CharacterWidget` in `celebrating` state
- [ ] Display next session teaser: next concept title and scheduled review date (sourced from SummaryAgent Firestore write)
- [ ] Log `session_complete` Firebase Analytics event on screen load
- [ ] Navigate back to Home on tap or after a short delay

### 5.9 Integration & Device Testing
- [ ] Run full session flow (happy path and help path) end-to-end on iOS Simulator
- [ ] Run full session flow end-to-end on Android Emulator
- [ ] Test on a physical iOS device (TestFlight build)
- [ ] Test on a physical Android device (APK sideload)
- [ ] Test app backgrounding mid-session: verify Hive cache restores session state correctly on resume
- [ ] Verify `CharacterWidget` transitions are smooth and correct across all screens and emotion changes
- [ ] Fix all critical bugs found in device testing before proceeding

### 5.10 Internal Testing (3–5 People)
- [ ] Distribute build to 3–5 internal testers via TestFlight (iOS) and direct APK (Android)
- [ ] Collect structured feedback on: session flow clarity, character appeal, quiz difficulty calibration, any crashes or confusing UI moments
- [ ] Review Firebase Crashlytics for crash reports from internal testers
- [ ] Review Firebase Analytics for gaps in the event funnel (e.g. `session_start` fires but `session_complete` does not)
- [ ] Address all critical feedback before proceeding to Phase 6

---

## Phase 6 — Trial Launch & Iteration

> **Goal:** App live on TestFlight and Google Play Internal Testing. 20–50 trial learners onboarded. Iterate on content and characters based on real usage data.

### 6.1 Store Preparation
- [ ] Decide on app name and brand before this phase — required for store listings
- [ ] Create App Store Connect listing: app name, description, screenshots, privacy policy URL
- [ ] Create Google Play Console listing: app name, description, screenshots, privacy policy
- [ ] Generate production signing certificates and provisioning profiles (iOS)
- [ ] Generate release keystore (Android)
- [ ] Build production release builds for both platforms and smoke test each

### 6.2 TestFlight & Play Internal Testing Launch
- [ ] Submit iOS build to TestFlight — resolve any App Store review issues
- [ ] Submit Android build to Google Play Internal Testing track
- [ ] Invite 20–50 trial learners via TestFlight invite link and Play internal testing link
- [ ] Set up a lightweight feedback channel for trial learners (e.g. Google Form or Discord)

### 6.3 Monitoring & Analytics
- [ ] Set up a monitoring dashboard (Firebase console or Looker Studio) tracking:
  - Session completion rate
  - Average quiz scores per module
  - Help trigger rate (% of sessions where HelpAgent activated)
  - `gemini_handoff_used` rate per lesson — disproportionately high rate signals a LessonAgent content or clarity problem
  - Day-1 and Day-7 return rate
- [ ] Set up Cloud Run error alerting: Cloud Monitoring alert policy on 5xx error rate threshold
- [ ] Set up Cloud SQL storage and CPU utilisation alerts

### 6.4 Content & Prompt Iteration
- [ ] Review quiz score data by lesson: flag any lesson with a high failure rate for content revision
- [ ] Review `gemini_handoff_used` rate by lesson: high rate = LessonAgent explanation quality issue for that concept
- [ ] Revise, re-generate, re-embed, and re-seed content chunks for all flagged lessons
- [ ] Iterate on LessonAgent and HelpAgent system prompt dialogue quality based on learner feedback

### 6.5 Remaining Character Assets (If Deferred)
- [ ] Generate or commission remaining character assets for any modules deferred from Phase 2
- [ ] Optimise, bundle, and ship as an app update via TestFlight / Play Internal Testing

### 6.6 Pre-Scale Preparation (Triggered When Learner Count Approaches 100)
- [ ] Add BigQuery streaming export from Firestore for deeper analytics (see spec §10)
- [ ] Add push notification support if Day-7 return rate data shows significant drop-off
- [ ] Review Cloud SQL instance sizing — upgrade from `db-f1-micro` if query latency degrades under load
- [ ] Review cost actuals against cost model (~$8–11/month target at 100 active learners)

---

*Roadmap based on system specification v1.0, updated March 2026 to reflect the 4-agent architecture (ContextAgent, LessonAgent, HelpAgent, SummaryAgent). RAGAgent and SchedulerAgent demoted to Python tools; QuizAgent absorbed into LessonAgent. Phases 0–4 are backend-first to ensure the knowledge base and agent pipeline are validated before significant Flutter investment.*