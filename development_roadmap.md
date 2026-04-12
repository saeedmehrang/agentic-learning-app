# Development Roadmap
## Agentic Self-Paced Learning System — MVP v1.1

---

## Phase Progress

| Phase | Title | Status |
|---|---|---|
| Phase 0 | GCP & Firebase Setup | ☐ |
| Phase 1 | Content Generation | ☐ |
| Phase 2 | Character Asset Production | ☐ |
| Phase 3 | Backend Simplification Refactor | 🔄 PR-1 ✅ PR-2 ✅ |
| Phase 4 | Integration & Load Testing | ☐ |
| Phase 5 | Flutter App | ☐ |
| Phase 6 | Trial Launch & Iteration | ☐ |

> Replace `☐` with `✅` when a phase is complete.

---

## Development Environment

**Devcontainer**: use for all backend work — Python, content pipeline, infra scripts, GCP CLI.
**Host machine**: use for Flutter — iOS tooling requires macOS; Android emulation inside containers is painful. Connect to the containerised backend via localhost port-forwarding.

---

## Phase 0 — GCP & Firebase Setup

> **Goal:** All cloud services provisioned, IAM configured, local dev environment ready.

### 0.1 GCP Project Bootstrap
- [x] Create GCP project and enable billing with a monthly budget alert
- [x] Enable required APIs: Cloud Run, Secret Manager, Artifact Registry, Firestore, Gemini
- [x] Create Cloud Run service account with least-privilege IAM roles
- [x] Store all secrets in Secret Manager — no plaintext credentials in code

### 0.2 Firebase Project Setup
- [x] Create Firebase project via Firebase Console
- [x] Provision Firebase Auth (Anonymous + Google Sign-In), Firestore (Native mode, us-central1)
- [x] Terraform writes `google-services.json` / `GoogleService-Info.plist` to Flutter app directories
- [ ] Enable Firebase Analytics and Crashlytics via Firebase Console (cannot be done via Terraform)

### 0.3 Local Development Environment
- [x] GCP SDK, Python dependencies, devcontainer configured
- [x] `.env.example` committed; real `.env` gitignored
- [x] Git repository with branch protection on `main`
- [ ] Flutter SDK set up on host machine with iOS Simulator + Android Emulator

---

## Phase 1 — Content Generation

> **Goal:** All 29 lessons × 3 tiers generated, reviewed, and approved in `pipeline/approved/`. This is the terminal artefact — no embedding or database loading step exists in the new architecture.

### 1.1 Course Structure & Outlines
- [x] 29-lesson outline in `courses/linux-basics/outlines.yaml`
- [x] Prerequisite graph defined (`prerequisites[]` per lesson)
- [x] `courses/linux-basics/concept_map.json` generated and human-reviewed

### 1.2 Content Generation Pipeline
- [x] `content-generation/generate_content.py` — reads outlines + concept map, calls Gemini per lesson × tier, writes to `pipeline/generated/`
- [x] Prompt templates in `courses/linux-basics/prompts/`
- [ ] **OPERATIONAL**: Run generation — `python content-generation/generate_content.py --resume`
  - 87 total calls (29 lessons × 3 tiers)
- [ ] **OPERATIONAL**: Human review — move approved files to `pipeline/approved/{tier}/L##.json`
- [ ] **OPERATIONAL**: Validate quiz questions — every question has `answer`, `options[]`, `explanation`; all formats are tap-to-select

### 1.3 Full Course Generation via Cloud Run Job

> **When to do this:** after PR-1 (decommission) and PR-2 (cache_manager) are merged. PR-1 removes the old embed/seed pipeline so the job image should be rebuilt clean. PR-2 defines the block layout so you know the generated content will be loadable.
>
> **Why Cloud Run Job, not local:** output goes straight to GCS where the backend reads at startup. No local → GCS sync step. Local generation is only for prompt iteration.
>
> **Why all 29 lessons now:** context caching requires ≥32 K tokens per block (Vertex AI minimum). 2 lessons × 3 tiers ≈ 6 K tokens — not enough to test caching. Generating all 29 now costs ~$0.20, is idempotent via `--resume`, and unblocks Phase 4 cache testing. Human review can happen in parallel with PR-3 and PR-4 implementation.

- [ ] **OPERATIONAL**: Rebuild and push the content-generation image (picks up PR-1 Dockerfile changes):
  ```bash
  gcloud builds submit --config infra/cloudbuild/content-generate.yaml .
  ```
- [ ] **OPERATIONAL**: Run generation for all remaining lessons (skips the 2 already generated):
  ```bash
  gcloud run jobs execute content-generate \
    --region us-central1 \
    --args="--resume" \
    --wait
  ```
  Expected: 81 new files generated (87 total − 6 already done), model = `gemini-2.5-flash`, ~$0.18 cost
- [ ] **OPERATIONAL**: Human review — read generated files in GCS (`pipeline/generated/`), move approved to `pipeline/approved/{tier}/L##.json`; flag any for regeneration with `--lesson L## --tier Beginner`
- [ ] **OPERATIONAL**: Validate quiz questions — every question has `answer`, `options[]`, `explanation`; all formats are tap-to-select

### 1.4 Content Verification

No embedding or database loading required. Approved JSON files are the terminal artefact.

- [ ] Verify all 87 approved files exist in GCS:
  ```bash
  gsutil ls "gs://$GCS_PIPELINE_BUCKET/linux-basics/pipeline/approved/**/*.json" | wc -l
  # → expect 87
  ```
- [ ] Spot-check 3–5 files for correct JSON structure (`lesson`, `quiz` keys present, `questions` non-empty)
- [ ] Confirm `outlines.yaml` and `concept_map.json` are current and consistent with approved content

---

## Phase 2 — Character Asset Production

> **Goal:** 54 PNGs (9 characters × 6 emotions) approved, optimised, and bundled in Flutter. Runs in parallel with Phase 1.

### 2.1 Style Guide & Character Definitions
- [ ] Define Module 9 character (Scrippy — scroll/parchment with quill)
- [ ] Write style anchor prompt for each of 9 characters — committed to `assets/characters/style_anchors.md`

### 2.2 Asset Generation
- [ ] Generate all 6 emotions for **Cursor** (Module 2) first as validation
- [ ] Review Cursor side-by-side: colour, proportions, line weight, transparent background
- [ ] Generate Tux Jr. (Module 1) and Filo (Module 3) — review before proceeding
- [ ] Generate remaining 6 characters once Modules 1–3 are approved
- [ ] Final review: all 54 PNGs approved

### 2.3 Export & Bundle
- [ ] Export as PNG, transparent background, named `{character_id}_{emotion}.png`
- [ ] Run `pngquant` or `optipng` — target under 80 KB each, total bundle under 4.5 MB
- [ ] Place in `assets/characters/`, declare all 54 in `pubspec.yaml`

---

## Phase 3 — Backend Simplification Refactor

> **Goal:** Remove ADK, pgvector RAG, and Cloud SQL dependencies. Replace with pure-Python scheduler, direct Gemini SDK sessions, and GCS-backed content loading. HTTP API surface unchanged — Flutter app requires no changes.

See `notes/simplification-plan-remove-rag-adk.md` for full design rationale.

---

### PR-1: Decommission Dead Code & Infra ✅ merged

**What was done:**
- Deleted all 4 ADK agents, `pipeline.py`, `search_knowledge_base.py`, `get_course_structure.py`, all embed/seed/validate scripts, agent tests, integration pipeline test
- Removed `google-adk`, `psycopg2-binary`, `google-cloud-aiplatform` from dependencies
- Cleaned `config.py`, `.env.example`, `push_secrets.sh`, `main.tf`, `enable_apis.sh`, `teardown.sh`
- Archived SQL migrations to `infra/sql/archive/`; deleted `cloudsql.tf`
- Fixed OTel import bug: `opentelemetry.exporter.gcp_trace` → `opentelemetry.exporter.cloud_trace`
- Added 77 tests: full HTTP session lifecycle (47), FSRS edge cases (23), logging config (14)
- GCP: Cloud SQL instance, VPC peering, DB secrets, IAM bindings destroyed via Terraform
  - Note: `google_service_networking_connection` and `google_compute_global_address` had to be removed from Terraform state (`terraform state rm`) after Cloud SQL deletion — GCP's service networking API blocks VPC peering deletion until its internal cleanup completes (~hours). Resources are gone from GCP but no longer tracked in state.

**Notes for PR-2+:**
- `main.py` has 5 `# TODO PR-X` stub comments marking where real implementations wire in
- `ty` (Astral) is the type checker in use — installed in backend `.venv`, not `mypy`
- Always activate `.venv` before running Python tools: `source backend/.venv/bin/activate`
- `support_email` in `terraform.tfvars` has no matching variable declaration — harmless warning, fix in a later infra PR

---

### PR-2: `scheduler.py` + `cache_manager.py` ✅ merged

**What was done:**
- `scheduler.py`: pure-Python `pick_next_lesson()` — overdue-first selection, mastery fallback, tier thresholds (<0.4 beginner, <0.75 intermediate), module→character mapping for all 9 modules
- `cache_manager.py`: Gemini context cache builder — 3 blocks (L01–L10, L11–L20, L21–L29), 1-hour TTL, lazy refresh on expiry, safe no-op when `ENABLE_LESSON_CACHE=false`
- Hardened both against malformed Firestore inputs: missing `lesson_id`, non-numeric `mastery_score`, timezone-naive datetimes, partial cache build failures
- Fixed `run_fsrs`: raises `ValueError` for `fsrs_stability <= 0` (would have silently produced past `next_review_at`)
- 174 tests passing (57 new edge-case tests added)

**Notes for PR-3+:**
- `cache_manager.get_cache(lesson_id)` returns `None` when disabled — `LessonSession.__init__` must handle `None` gracefully (don't pass it to `GenerativeModel` constructor)
- `scheduler.pick_next_lesson()` signature is `(concepts: list[dict]) -> dict` — the Firestore concepts sub-collection fetch happens in `main.py` (PR-5), not in the scheduler
- Tier thresholds: mastery < 0.4 → beginner, < 0.75 → intermediate, ≥ 0.75 → advanced — `LessonSession` must use the `tier` field from the scheduler result to select the correct approved JSON file

---

### PR-3: `lesson_session.py`

**Goal:** Implement stateful multi-turn Gemini chat wrapping lesson teaching, quiz loop, and HelpSession (3-turn cap).

**Requirements before opening:** PR-2 merged. At least a few approved lesson JSON files in `pipeline/approved/` for smoke testing.

**Files created:**
- `backend/lesson_session.py` — `LessonSession` class
  - `__init__`: loads lesson content from in-memory store (set at startup), builds system prompt with `outlines.yaml` + `concept_map.json` + current lesson JSON, starts `GenerativeModel.start_chat()` (with `cached_content` handle if available)
  - `teach() -> dict` — Turn 1: returns `{lesson_text, character_emotion_state, key_concepts}`
  - `next_question() -> dict` — returns `{question_text, format, options, character_emotion_state}`
  - `evaluate_answer(answer: str) -> dict` — returns `{correct, explanation, concept_score_delta, character_emotion_state, trigger_help?}`
  - Tracks consecutive wrong answers per concept; sets `trigger_help: True` on 2nd consecutive wrong
  - `HelpSession` nested class (or separate `help_session.py`):
    - `respond(message: str) -> dict` — returns `{resolved, character_emotion_state, gemini_handoff_prompt?}`
    - `increment_turn()` raises `RuntimeError` after turn 3 (hard cap enforced in Python, not prompt)
    - Uses separate `start_chat()` with lesson context injected as first message
  - All JSON outputs validated against expected keys before returning

**Files created (tests):**
- `backend/tests/test_lesson_session.py` — unit tests with mocked `google.generativeai.GenerativeModel`:
  - Teach phase returns correct schema
  - Quiz question returns correct format
  - Correct answer: `correct=True`, positive delta, `celebrating` emotion
  - Wrong answer once: `correct=False`, `encouraging`, no trigger
  - Wrong answer twice: `trigger_help=True`
  - Help resolved at turn 2: `resolved=True`
  - Help unresolved at turn 3: `resolved=False`, `gemini_handoff_prompt` present
  - Help turn 4 attempt: `RuntimeError` raised

**Definition of Done:**
```bash
cd backend && ruff check . && mypy .
python -m pytest backend/tests/test_lesson_session.py -v
# all 8+ tests pass; Gemini SDK mocked throughout
```

---

### PR-4: `summary_call.py` + Firestore writes

**Goal:** Implement single-shot Gemini summary call, FSRS update, and Firestore session record write.

**Requirements before opening:** PR-3 merged.

**Files created:**
- `backend/summary_call.py` — `run_summary(session_data: dict) -> dict`
  - Single `generate_content()` call to Gemini 2.5 Flash-Lite (no chat history)
  - Input: `{lesson_id, tier, quiz_scores, time_on_task_seconds, help_triggered, gemini_handoff_used}`
  - Calls `run_fsrs()` for each concept touched
  - Returns session record dict matching Firestore schema
  - Firestore write: `learners/{uid}/sessions/{session_id}` + `learners/{uid}/concepts/{lesson_id}` update

**Files created (tests):**
- `backend/tests/test_summary_call.py` — unit tests with mocked Gemini SDK and mocked Firestore:
  - Session record contains all required fields with correct types
  - `next_review_at` is a future timestamp for a correct outcome
  - `gemini_handoff_used` is boolean (never the prompt string)
  - Firestore write called with correct paths

**Definition of Done:**
```bash
cd backend && ruff check . && mypy .
python -m pytest backend/tests/test_summary_call.py backend/tests/tools/test_run_fsrs.py -v
```

---

### PR-5: `main.py` Rewire

**Goal:** Remove all ADK imports and wiring from `main.py`. Connect all HTTP endpoints to the new modules. HTTP API surface unchanged.

**Requirements before opening:** PR-2, PR-3, PR-4 all merged.

**Files modified:**
- `backend/main.py`:
  - Remove all `google.adk` imports, `InMemorySessionService`, `Runner` instances
  - Import `scheduler`, `cache_manager`, `lesson_session`, `summary_call`
  - Lifespan: call `cache_manager.build_caches()` at startup; load all approved JSON files into in-memory dict
  - `POST /session/start {uid}`:
    1. Firestore read (learner profile + concepts)
    2. `scheduler.pick_next_lesson(concepts)` → `{lesson_id, tier, character_id}`
    3. Create `LessonSession`, store in `_sessions[session_id]`
    4. Return `{session_id, lesson_id, character_id, tier}`
  - `GET /session/{id}/lesson` → `session.teach()`
  - `GET /session/{id}/quiz/question` → `session.next_question()`
  - `POST /session/{id}/quiz/answer {answer}` → `session.evaluate_answer(answer)`
  - `POST /session/{id}/help {message}` → `session.help.respond(message)` (checks cap before calling)
  - `POST /session/{id}/complete` → `summary_call.run_summary(session_data)`; delete `_sessions[session_id]`
  - Keep all OpenTelemetry / Cloud Trace instrumentation unchanged

**Definition of Done:**
```bash
cd backend && ruff check . && mypy .
python -m pytest backend/tests/ -v  # full test suite passes (unit + integration stubs)

# Integration tests — require ADC credentials + deployed Cloud Run URL
# Run these after deploying to Cloud Run in Phase 4.2, not locally
# backend/tests/integration/ — add full session flow tests here in PR-5

# Smoke test — start server locally
uvicorn main:app --reload &

curl -s -X POST http://localhost:8000/session/start \
  -H "Content-Type: application/json" \
  -d '{"uid": "test-uid-001"}' | jq .
# → { session_id, lesson_id, character_id, tier }

SESSION_ID=<from above>

curl -s http://localhost:8000/session/$SESSION_ID/lesson | jq .
# → { lesson_text, character_emotion_state, key_concepts }

curl -s http://localhost:8000/session/$SESSION_ID/quiz/question | jq .
# → { question_text, format, options, character_emotion_state }

curl -s -X POST http://localhost:8000/session/$SESSION_ID/quiz/answer \
  -H "Content-Type: application/json" \
  -d '{"answer": "A"}' | jq .
# → { correct, explanation, concept_score_delta, character_emotion_state }

curl -s -X POST http://localhost:8000/session/$SESSION_ID/complete \
  -H "Content-Type: application/json" \
  -d '{"time_on_task_seconds": 300}' | jq .
# → { summary_text, fsrs_result, lesson_id }

# Verify no ADK references remain in main.py
grep -n "adk\|InMemorySession\|Runner\|context_agent\|lesson_agent\|help_agent\|summary_agent" backend/main.py
# → must return no matches
```

---

## Phase 4 — Integration & Load Testing

> **Goal:** Full session pipeline tested end-to-end against the deployed Cloud Run service. No Flutter required.

### 4.1 Cloud Run Deployment
- [ ] **OPERATIONAL**: Build and push backend image:
  ```bash
  gcloud builds submit --config infra/cloudbuild/backend.yaml .
  ```
- [ ] **OPERATIONAL**: Deploy to Cloud Run:
  ```bash
  gcloud run deploy learning-backend \
    --image us-central1-docker.pkg.dev/agentic-learning-app-e13cb/agentic-learning/backend:latest \
    --region us-central1 --platform managed --allow-unauthenticated \
    --min-instances 0 \
    --service-account cloud-run-app-identity@agentic-learning-app-e13cb.iam.gserviceaccount.com \
    --set-env-vars APP_ENV=production,GCP_PROJECT_ID=agentic-learning-app-e13cb,ENABLE_LESSON_CACHE=false \
    --set-secrets GEMINI_API_KEY=GEMINI_API_KEY:latest
  ```
- [ ] Verify health check: `curl https://<cloud-run-url>/health` → `{"status":"ok"}`

### 4.2 End-to-End Session Tests

> These are backend integration tests hitting the deployed Cloud Run service with real Firestore and Gemini calls. Add them to `backend/tests/integration/` in PR-5. They require `CLOUD_RUN_URL` and ADC credentials — do not run locally without real GCP context.

- [ ] Write `backend/tests/integration/test_session_e2e.py`:
  - Happy path: `start → lesson → quiz question → quiz answer (correct) → complete`; verify Firestore `next_review_at` is a future timestamp
  - Help path (resolved): 2 wrong answers → `trigger_help=True` → help turn 1–2 → `resolved=True` → quiz resumes
  - Help path (unresolved): 3 help turns → `resolved=False`, `gemini_handoff_prompt` non-empty, `gemini_handoff_used=True` in summary
  - New learner: `POST /session/start` for unknown UID → creates Firestore profile, returns `lesson_id=L01`
  - FSRS write: after `complete`, `learners/{uid}/concepts/{lesson_id}.next_review_at` is a valid future ISO 8601 timestamp
- [ ] Run integration tests against deployed Cloud Run:
  ```bash
  CLOUD_RUN_URL=https://<service-url> python -m pytest backend/tests/integration/ -v
  ```

### 4.3 Load Test
- [ ] Simulate 10 concurrent sessions against Cloud Run
- [ ] Measure cold start latency (target: `POST /session/start` < 100 ms excluding Gemini generation)
- [ ] Confirm scale-to-zero: after 15 minutes idle, `gcloud run services describe` shows 0 instances

### 4.4 Enable and Test Cache (Optional)
- [ ] `gcloud run services update learning-backend --update-env-vars ENABLE_LESSON_CACHE=true`
- [ ] Verify startup logs show "block_0 cache created", "block_1 cache created", "block_2 cache created"
- [ ] Run a lesson turn and confirm Cloud Trace shows no prefix token charges (cached)
- [ ] Verify cache is shared: two concurrent sessions on the same block use the same cache handle

---

## Phase 5 — Flutter App

> **Goal:** A fully working Flutter app connected to the deployed Cloud Run backend. All screens implemented. Full session flow tested on real devices.

### 5.1 App Skeleton & Auth
- [ ] Flutter project with correct bundle IDs for iOS and Android
- [ ] Firebase Anonymous Auth on first launch; Riverpod auth state provider
- [ ] Hive for local session cache; `http` base client with auth token injection
- [ ] Register all 6 Firebase Analytics events: `session_start`, `lesson_complete`, `quiz_answer`, `help_triggered`, `gemini_handoff_tapped`, `session_complete`
- [ ] Firebase Crashlytics configured

### 5.2 Character Widget
- [ ] `CharacterWidget` — `AnimatedCrossFade` (300ms) driven by `emotion_state` string
- [ ] Asset loader: `{module_character_id}_{emotion_state}.png` from bundled assets
- [ ] Corner mode (80×80dp top-right) and help mode (120×120dp centred)
- [ ] Test all 54 asset combinations — no missing assets, no layout overflow

### 5.3 Onboarding & Splash
- [ ] Splash: anonymous sign-in fires here
- [ ] Onboarding (first-launch): 3-question difficulty tier quiz → write to Firestore learner profile
- [ ] Route logic: `onboarding_complete == false` → Onboarding; else → Home

### 5.4 Home / Dashboard
- [ ] Progress overview, next lesson card with character thumbnail, streak counter
- [ ] Google Sign-In upgrade prompt (non-blocking bottom sheet, after session 3)

### 5.5 Session Screen — Lesson Phase
- [ ] `POST /session/start` → display lesson text, drive CharacterWidget emotion
- [ ] Skeleton loader while waiting for backend response
- [ ] "Ready to be quizzed?" CTA to advance

### 5.6 Session Screen — Quiz Phase
- [ ] All 4 question formats: MC (4 options), TF (2 buttons), Fill (tap-to-select), Command (tap-to-select)
- [ ] Submit answer → drive CharacterWidget emotion from response
- [ ] `trigger_help: true` → activate Help Bottom Sheet

### 5.7 Help Bottom Sheet & Gemini Referral Card
- [ ] Help Bottom Sheet: slides up, CharacterWidget centres to 120×120dp
- [ ] Render HelpSession turn exchanges as conversation bubbles
- [ ] "Got it" dismisses sheet, returns CharacterWidget to corner
- [ ] `resolved: false` after turn 3 → Gemini Referral Card with `url_launcher` deep-link
- [ ] Log `gemini_handoff_tapped` on tap

### 5.8 Session Complete Screen
- [ ] Summary text, CharacterWidget in `celebrating`, next session teaser
- [ ] Log `session_complete` event; navigate to Home

### 5.9 Integration & Device Testing
- [ ] Full happy path and help path on iOS Simulator + Android Emulator
- [ ] Physical device test: iOS (TestFlight) + Android (APK sideload)
- [ ] App backgrounding mid-session: Hive restores state correctly on resume
- [ ] `flutter analyze` — zero warnings

**Definition of Done:**
```bash
flutter analyze          # zero warnings
flutter test             # all widget tests pass
# Manual: full session flow on physical iOS + Android device
```

---

## Phase 6 — Trial Launch & Iteration

> **Goal:** App live on TestFlight and Google Play Internal Testing. 20–50 trial learners. Iterate on content and characters based on real usage.

### 6.1 Store Preparation
- [ ] App name and brand decided
- [ ] App Store Connect listing: description, screenshots, privacy policy URL
- [ ] Google Play Console listing
- [ ] iOS production signing certificates; Android release keystore

### 6.2 Launch
- [ ] Submit iOS build to TestFlight; resolve any review issues
- [ ] Submit Android build to Google Play Internal Testing
- [ ] Invite 20–50 trial learners; set up feedback channel

### 6.3 Monitoring
- [ ] Firebase Analytics dashboard: session completion rate, quiz scores per module, help trigger rate, `gemini_handoff_used` rate, Day-1 and Day-7 return rate
- [ ] Cloud Run error alerting: 5xx rate threshold alert
- [ ] Review Crashlytics for crash reports from trial users

### 6.4 Content Iteration
- [ ] Flag lessons with high quiz failure rate for content revision
- [ ] Flag lessons with high `gemini_handoff_used` rate (explanation quality issue)
- [ ] Regenerate and re-approve flagged lessons; backend picks up new files on next Cloud Run deployment

### 6.5 Pre-Scale Preparation (at ~100 learners)
- [ ] Enable `ENABLE_LESSON_CACHE=true` on Cloud Run
- [ ] Add BigQuery streaming export from Firestore for deeper analytics
- [ ] Add push notifications if Day-7 return rate shows significant drop-off
- [ ] Review cost actuals against cost model (~$2.35/month target at 100 learners without cache, ~$0.90 with)

---

*Roadmap v1.1 — updated April 2026. Phase 3 replaced ADK scaffold with Backend Simplification Refactor (5 PRs). Cloud SQL and pgvector decommissioned. Full decision trail in `notes/simplification-plan-remove-rag-adk.md`.*
