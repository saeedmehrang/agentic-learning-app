# Development Roadmap
## Agentic Self-Paced Learning System — MVP v1.1

---

## Phase Progress

| Phase | Title | Status |
|---|---|---|
| Phase 0 | GCP & Firebase Setup | ☐ |
| Phase 1 | Content Generation | ✅ |
| Phase 2 | Character Asset Production | ☐ |
| Phase 3 | Backend Simplification Refactor | ✅ PR-1 ✅ PR-2 ✅ PR-3 ✅ PR-4 ✅ PR-5 ✅ |
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
- [x] **OPERATIONAL**: Run generation — `python content-generation/generate_content.py --resume`
  - 87 total calls (29 lessons × 3 tiers)
- [x] **OPERATIONAL**: Human review — move approved files to `pipeline/approved/{tier}/L##.json`
- [x] **OPERATIONAL**: Validate quiz questions — every question has `answer`, `options[]`, `explanation`; all formats are tap-to-select

### 1.3 Full Course Generation via Cloud Run Job

> **When to do this:** after PR-1 (decommission) and PR-2 (cache_manager) are merged. PR-1 removes the old embed/seed pipeline so the job image should be rebuilt clean. PR-2 defines the block layout so you know the generated content will be loadable.
>
> **Why Cloud Run Job, not local:** output goes straight to GCS where the backend reads at startup. No local → GCS sync step. Local generation is only for prompt iteration.
>
> **Why all 29 lessons now:** context caching requires ≥32 K tokens per block (Vertex AI minimum). 2 lessons × 3 tiers ≈ 6 K tokens — not enough to test caching. Generating all 29 now costs ~$0.20, is idempotent via `--resume`, and unblocks Phase 4 cache testing. Human review can happen in parallel with PR-3 and PR-4 implementation.

- [x] **OPERATIONAL**: Rebuild and push the content-generation image (picks up PR-1 Dockerfile changes)
- [x] **OPERATIONAL**: Run generation for all 29 lessons via Cloud Run Job — all 87 files generated
- [x] **OPERATIONAL**: Human review complete — approved files in GCS `gs://agentic-learning-pipeline/linux-basics/pipeline/approved/`
- [x] **OPERATIONAL**: Validate quiz questions — every question has `answer`, `options[]`, `explanation`; all formats are tap-to-select

### 1.4 Content Verification

No embedding or database loading required. Approved JSON files are the terminal artefact.

- [x] Verify all 87 approved files exist in GCS: `gs://agentic-learning-pipeline/linux-basics/pipeline/approved/`
- [x] Spot-check 3–5 files for correct JSON structure (`lesson`, `quiz` keys present, `questions` non-empty)
- [x] Confirm `outlines.yaml` and `concept_map.json` are current and consistent with approved content

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

**Notes for PR-3+ (historical — resolved in PR-3):**
- `cache_manager.get_cache(lesson_id)` returns `None` when disabled — handled in `LessonSession.__init__` by passing `cached_content=handle.name` only when non-None
- `scheduler.pick_next_lesson()` signature is `(concepts: list[dict]) -> dict` — the Firestore concepts sub-collection fetch happens in `main.py` (PR-5), not in the scheduler
- Tier thresholds: mastery < 0.4 → beginner, < 0.75 → intermediate, ≥ 0.75 → advanced — implemented in `LessonSession` via the `tier` field from the scheduler result

---

### PR-3: `lesson_session.py` + GCS content loading ✅ merged

**What was done:**

**GCS-only content loading (replaces local-filesystem approach):**
- `backend/config.py`: added `gcs_pipeline_bucket: str = ""` — reads `GCS_PIPELINE_BUCKET` env var
- `backend/pyproject.toml`: added `google-cloud-storage>=2.0` and `google-genai>=1.0` as explicit deps
- `backend/cache_manager.py`: refactored with GCS-aware loaders:
  - `_load_from_gcs(bucket_name)` — lists `linux-basics/pipeline/approved/` blobs, downloads all JSON
  - `_load_from_local(approved_dir)` — unchanged local loader (tests / local dev)
  - `_load_approved_content(approved_dir)` — routes to GCS or local based on config
  - `_load_yaml_from_gcs` / `_load_json_from_gcs` — fetchers for `outlines.yaml` + `concept_map.json`
  - `build_caches()` now returns `tuple[lesson_store, outlines, concept_map]` (previously `None`)
  - `_load_approved_files(path)` kept as backward-compat alias for existing tests
- `backend/main.py`: implemented the two `# TODO PR-2` lifespan stubs — calls `build_caches()` at startup, populates `_lesson_store`, `_outlines`, `_concept_map` module-level dicts

**`lesson_session.py`:**
- `LessonSession` — stateful multi-turn `google.genai` chat using `client.chats.create()`:
  - `__init__`: receives `lesson_content`, `outlines`, `concept_map` (injected from `main.py`); builds system prompt; passes `cached_content=cache_handle.name` in `GenerateContentConfig` when caching enabled (`None` is safe when disabled)
  - `teach() -> dict` — returns `{lesson_text, character_emotion_state, key_concepts}`
  - `next_question() -> dict` — returns `{question_text, format, options, character_emotion_state}`
  - `evaluate_answer(answer) -> dict` — returns `{correct, explanation, concept_score_delta, character_emotion_state, trigger_help}`; tracks consecutive wrong answers per concept (key = question index); `trigger_help=True` + `help_session` created on 2nd consecutive wrong
  - `help_session: HelpSession | None` — set automatically when `trigger_help` fires
- `HelpSession` — separate `client.chats.create()` with `help_model` (Flash-Lite); hard cap 3 turns; `RuntimeError` on 4th call; fallback `gemini_handoff_prompt` generated if Gemini omits it on turn 3
- `_extract_json()` — strips markdown fences, extracts JSON from Gemini response text
- `_validate_keys()` — raises `ValueError` on missing required keys (loud failures)
- `_require_text()` — raises `ValueError` if `response.text` is `None` (SDK safety block guard)

**Tests — 207 passing (33 new in test_lesson_session.py, 8 new in test_cache_manager.py):**
- Init with/without cache handle, question count
- teach() schema + missing-key error
- next_question() schema + IndexError when exhausted
- Correct answer: schema, index advances, consecutive-wrong counter resets
- First wrong: no trigger; second consecutive wrong: trigger_help=True, HelpSession created
- HelpSession: resolved turn 1, handoff on turn 3, RuntimeError turn 4, fallback handoff
- `_extract_json`: bare JSON, fenced, prose-wrapped, no-JSON error, invalid-JSON error
- GCS loader: blob parsing, skip non-JSON/malformed/unreadable blobs, routing, tuple return

**Operational step required before first Cloud Run deploy:**
```bash
gsutil cp courses/linux-basics/outlines.yaml \
  gs://agentic-learning-pipeline/linux-basics/outlines.yaml
gsutil cp courses/linux-basics/concept_map.json \
  gs://agentic-learning-pipeline/linux-basics/concept_map.json
```

**Notes for PR-4+:**
- Gemini SDK in use: `google.genai` (new SDK, `google-genai` package) — NOT `google.generativeai`. All new backend code must use `genai.Client()` + `client.chats.create()` pattern
- `LessonSession.help_session` is set on `trigger_help` — PR-5 `main.py` must store the `LessonSession` object on `SessionData` (or equivalent) so `/session/{id}/help` routes through `session.help_session.respond()`
- `_lesson_store`, `_outlines`, `_concept_map` are module-level in `main.py` — pass as constructor args to `LessonSession` in `session_start`
- `ty` (Astral) is the type checker — all PR-3 files pass `ty check`; pre-existing errors in older test files are not new

---

### PR-4: `summary_call.py` + Firestore writes ✅ merged

**What was done:**
- `backend/summary_call.py` — `run_summary(session_data: dict) -> dict`
  - Single `generate_content()` call to `settings.summary_model` (gemini-2.5-flash-lite); no chat history
  - Input: `{uid, session_id, lesson_id, tier, quiz_scores, time_on_task_seconds, help_triggered, gemini_handoff_used, concept_fsrs}`
  - `concept_outcomes` derived from Gemini response; falls back to `quiz_scores` if Gemini omits them
  - Calls `run_fsrs()` for each concept touched; guards against corrupt stability (≤ 0) by resetting to default
  - Returns full session record dict: `{session_id, uid, lesson_id, tier, quiz_scores, time_on_task_seconds, help_triggered, gemini_handoff_used, summary_text, concept_outcomes, fsrs_results, completed_at}`
  - Firestore write (best-effort, errors logged but not re-raised):
    - `learners/{uid}/sessions/{session_id}` — full session record via `.set()`
    - `learners/{uid}/concepts/{lesson_id}` — FSRS state per concept via `.set(merge=True)`
  - `gemini_handoff_used` coerced to `bool` before storage (privacy rule)
- `backend/tests/test_summary_call.py` — 20 unit tests with mocked Gemini SDK and Firestore:
  - All required session record fields and types
  - `next_review_at` is future for correct and incorrect outcomes
  - `gemini_handoff_used` is always `bool` (never prompt string)
  - Correct Firestore paths for session + concept documents
  - Concept update payload has all required FSRS fields (`fsrs_stability`, `fsrs_difficulty`, `mastery_score`, `next_review_at`, `last_reviewed_at`)
  - Fallback `concept_outcomes` derived from `quiz_scores`
  - Corrupt stability guard (≤ 0 reset to default)
  - Firestore errors swallowed
  - `completed_at` is valid ISO 8601 UTC
  - `session_id` auto-generated when absent

**Notes for PR-5:**
- `run_summary()` returns the full session record — `main.py` returns it directly as `summary` in `SessionCompleteResponse`
- The summary schema differs from the old stub: no `tier_used`, `quiz_questions_asked`, `quiz_correct` keys — these are tracked internally in `SessionData` but not forwarded to `run_summary`
- `quiz_scores` passed to `run_summary` are concept-level deltas accumulated during the quiz phase (keyed by question index in current impl)

---

### PR-5: `main.py` Rewire ✅ merged

**What was done:**
- `backend/main.py` — all stub TODO handlers replaced with real module calls:
  - Top-level `from lesson_session import LessonSession` (patchable in tests)
  - `SessionData` dataclass extended: `lesson_session: Any`, `quiz_scores: dict[str, float]` fields added
  - `_read_learner_concepts(uid)` — reads `learners/{uid}/concepts` sub-collection from Firestore; returns `[]` on any error (new learner falls back to L01/beginner via scheduler)
  - `POST /session/start`: Firestore read → `scheduler.pick_next_lesson(concepts)` → lesson content lookup from `_lesson_store` → `cache_manager.get_cache(lesson_id)` → `LessonSession(...)` stored in `_sessions`
  - `GET /session/{id}/lesson` → `data.lesson_session.teach()`; advances phase to `quiz`
  - `GET /session/{id}/quiz/question` → `data.lesson_session.next_question()`; increments `quiz_questions_asked`; maps `IndexError` to 409
  - `POST /session/{id}/quiz/answer` → `data.lesson_session.evaluate_answer(answer)`; tracks `quiz_correct`, accumulates `quiz_scores`; sets phase to `help` when `trigger_help=True`
  - `POST /session/{id}/help` → `data.lesson_session.help_session.respond(message)`; 409 if `help_session is None`; `RuntimeError` from HelpSession mapped to 409; phase reverts to `quiz` after cap or `resolved=True`
  - `POST /session/{id}/complete` → `summary_call.run_summary(session_input)`; deletes `_sessions[session_id]`
  - All OpenTelemetry / Cloud Trace instrumentation unchanged
  - Zero ADK references remain
- `backend/tests/conftest.py` — new shared autouse fixture patches all external I/O at the `main.py` boundary:
  - `main._read_learner_concepts` → `[]`
  - `main.LessonSession` → factory returning a fresh `MagicMock` per instantiation (teach/next_question/evaluate_answer return fixed payloads)
  - `summary_call.genai.Client` and `summary_call.firestore.Client` → MagicMocks
- `backend/tests/test_session_api.py` and `test_session_api_edge_cases.py` updated:
  - `_set_phase(sid, "help")` now also injects a mock `help_session` on the session's `lesson_session`
  - `test_returns_summary_with_expected_keys` updated to new `run_summary` schema
  - `test_quiz_stats_reflected_in_summary` updated (no longer checks `quiz_questions_asked` in summary)
- 227 tests passing

---

### PR-6: Gemini Handoff — Context-Rich Prompt + AI Studio Deep Link

> **Goal:** Make the Gemini handoff actually useful. Today `HelpSession` generates a prompt with almost no context — it doesn't know which quiz question the student failed, what their wrong answers were, or what the help turns covered. The fallback is literally "I was studying Linux and got stuck." A student pasting that into Gemini gets a generic response. This PR fixes the root cause and delivers the handoff reliably across all platforms using a confirmed, documented URL mechanism targeting Gemini Flash (free tier).

---

#### Problem Statement (precise)

At the moment `gemini_handoff_prompt` is generated (HelpSession turn 3), the model has:
- The full lesson JSON (in system prompt) ✅
- The 3 help-turn conversation (in chat history) ✅

But it does **not** have:
- The specific quiz question the student failed ❌
- The student's wrong answer(s) ❌
- The correct answer to the question ❌
- What the LessonSession's `teach()` output was (the character's explanation) ❌

Additionally, the original deep-link design (`gemini.google.com/app?text=`) was unvalidated and has no confirmed URL parameter support on mobile. It is replaced here with a confirmed, documented mechanism.

---

#### Delivery Mechanism: Google AI Studio URL (Confirmed)

**Research finding:** Google AI Studio (`aistudio.google.com`) supports URL-based prompt pre-filling via an officially documented `?prompt=` query parameter, confirmed by Google DeepMind's Philipp Schmid in April 2025:

```
https://aistudio.google.com/prompts/new_chat?prompt={encoded_text}&model=gemini-2.5-flash
```

This URL:
- **Works in any mobile browser** (no app install required, no deep-link uncertainty)
- **Pre-fills the prompt** in a new chat — student sees the full context and can tap Run
- **Targets Gemini 3 Flash** via the `?model=gemini-3-flash-preview` parameter — the free-tier model, no billing required for the student
- **Requires the student to be signed into their Google account** — which is standard for AI Studio
- **Works identically on iOS and Android** — it is a web URL, not a native app intent

This eliminates the entire iOS/Android deep-link uncertainty. `url_launcher` opens it in the system browser with `LaunchMode.externalNonBrowserApplication` attempted first; if unavailable, browser is used — both work correctly.

**Why not the Gemini consumer app (`gemini.google.com`)?** The consumer app has no documented URL parameter for prompt pre-filling. Extensions exist that enable it on desktop Chrome, but there is no confirmed mobile support. AI Studio is the correct surface for a context-rich technical handoff.

**Why Gemini 3 Flash (free)?** The `?model=gemini-3-flash-preview` parameter ensures the student lands on the free-tier model (15 RPM, 1,000 req/day — irrelevant for one handoff per session). No credit card, no quota concern. Gemini 3 Flash is more than sufficient for tutoring continuation.

---

#### Design: Three Changes

**1. Inject full quiz-failure context into HelpSession at creation time**

When `LessonSession.evaluate_answer()` creates a `HelpSession` (on the 2nd consecutive wrong answer), it currently passes only `self._lesson_content`. It will also pass:

- `failed_question: dict` — the full question dict (text, format, options)
- `correct_answer: str` — the correct answer to the failed question (from the question dict)
- `student_wrong_answers: list[str]` — the student's two wrong answers
- `lesson_teach_text: str` — the character's explanation from `teach()` (stored on `self` after turn 1)

`HelpSession.__init__` will receive these and inject them into the system prompt via `_build_help_system_prompt()`. No new network calls. No added latency. This is purely passing already-in-memory data.

**2. Improve `_build_help_system_prompt()` to produce a structured, self-contained handoff prompt**

Update the system prompt instruction for the turn-3 unresolved case to require this explicit structure in `gemini_handoff_prompt`:

> "The `gemini_handoff_prompt` must be a complete, self-contained prompt a student can submit to a brand-new Gemini conversation with zero additional context. It must include:
> (a) The Linux concept being studied (module name, lesson ID)
> (b) The original lesson explanation that was given to the student (verbatim summary)
> (c) The exact quiz question the student got wrong (verbatim)
> (d) The correct answer to that question
> (e) The student's wrong answer(s)
> (f) A brief summary of what the 3 help turns attempted and why the student is still confused
> (g) A direct instruction to Gemini: 'Please explain this concept differently, using a fresh analogy. Then ask me a simple question to check my understanding.'
> Write this as if you are handing off a student to a new tutor who has seen nothing."

Correct answers are included because Gemini needs to know what to reinforce, not just what went wrong. Without the correct answer in context, Gemini may spend time on incorrect explanations.

**3. AI Studio URL construction and delivery in Flutter**

The Flutter `GeminiReferralCard` widget constructs the AI Studio URL at tap time:

```dart
final encoded = Uri.encodeComponent(handoffPrompt);
final url = Uri.parse(
  'https://aistudio.google.com/prompts/new_chat'
  '?prompt=$encoded'
  '&model=gemini-3-flash-preview'
);
await launchUrl(url, mode: LaunchMode.externalApplication);
```

**Prompt length cap: 3,000 characters.** AI Studio's URL bar handles long queries without issues in modern browsers, but 3,000 chars is a practical limit that comfortably fits the full structured context (question + correct answer + wrong answers + explanation summary + instruction). Truncate at the last complete sentence before the limit, appending `"[see lesson {lesson_id} for full context]"`.

**Fallback:** If `launchUrl()` fails (rare — this is a plain HTTPS URL), copy to clipboard via `Clipboard.setData()` and show a `SnackBar`: *"Prompt copied — paste it into AI Studio to continue learning."*

---

#### Files Changed

| File | Change |
|---|---|
| `backend/lesson_session.py` | `LessonSession`: store `_teach_text` after `teach()`; pass `failed_question`, `correct_answer`, `student_wrong_answers`, `teach_text` to `HelpSession.__init__()` |
| `backend/lesson_session.py` | `HelpSession.__init__`: accept new args; `_build_help_system_prompt()` injects all of them |
| `backend/lesson_session.py` | System prompt instruction for turn-3 handoff: specify required prompt structure including correct answer (see above) |
| `app/lib/widgets/gemini_referral_card.dart` | AI Studio URL construction with `?prompt=` + `?model=gemini-2.5-flash`; 3,000-char cap; clipboard fallback |
| `backend/tests/test_lesson_session.py` | Tests: handoff prompt contains question text; contains correct answer; contains at least one wrong answer; fallback contains lesson ID |
| `app/test/widgets/gemini_referral_card_test.dart` | Tests: URL contains `aistudio.google.com`; URL contains `model=gemini-2.5-flash`; fallback copies to clipboard; prompt truncated at 3,000 chars |

---

#### What Is Explicitly Not Done in This PR

- **No increase to the 3-turn cap.** The turn limit is a pedagogical constraint. The right fix for a student who is stuck is a better handoff, not more turns in a broken context.
- **No logging of handoff prompt content.** `gemini_handoff_used` remains a boolean in analytics only. The prompt text is never written to Firestore or Firebase Analytics.
- **No server-side URL construction.** The backend returns `gemini_handoff_prompt` as a plain string. URL construction, encoding, and launch logic live entirely in Flutter.

---

#### Verification

1. Unit test: trigger a help session; assert `gemini_handoff_prompt` contains the `question_text` verbatim.
2. Unit test: assert `gemini_handoff_prompt` contains the `correct_answer` string.
3. Unit test: assert `gemini_handoff_prompt` contains at least one of the student's wrong answers.
4. Flutter widget test: assert constructed URL starts with `https://aistudio.google.com/prompts/new_chat`.
5. Flutter widget test: assert URL contains `model=gemini-3-flash-preview`.
6. Flutter widget test: pass a 4,000-character prompt; assert URL-encoded prompt is ≤ 3,000 characters with truncation suffix.
7. Flutter widget test: mock `launchUrl` to throw; assert clipboard is populated and SnackBar appears.
8. Manual (Android + iOS): tap referral card → browser opens AI Studio with prompt pre-filled in the new chat box, model set to Gemini 2.5 Flash.

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
