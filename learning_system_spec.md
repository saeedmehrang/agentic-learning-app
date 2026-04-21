# Agentic Self-Paced Learning System
## Full Technical Specification — MVP v1.1
*Flutter · Direct Gemini SDK · GCP-Native · Frugal Stack*
*Updated April 2026 — Architecture simplified: ADK and pgvector RAG removed*

---

## 1. Product Vision & Design Principles

This system is a self-paced, agentic learning platform for smartphone users. It guides learners through structured curricula using adaptive pacing, spaced repetition scheduling, persistent companion characters (one per topic module), and short conversational interventions when a learner struggles. The MVP focuses exclusively on a Linux basics course for general adult learners.

| Principle | What it means in practice |
|---|---|
| Bite-sized by default | Every session is 7–10 minutes. Each session covers exactly one concept cluster. |
| Character-as-memory-hook | Each course module has a dedicated character. The character is not chosen by the learner — it is assigned to the module. Seeing that character triggers retrieval of that topic cluster. This is an intentional spaced memory technique. |
| Short conversations only | LLM dialogue is capped at 3 turns. All other character speech is templated. After turn 3, if unresolved, the learner is offered a handoff to the Gemini app with a pre-filled contextual prompt. |
| Spaced repetition at core | The FSRS algorithm drives lesson scheduling. Mastery is tracked per concept, not per lesson. |
| Frugal by design | Every architectural choice optimises for minimum cost at 100–1,000 active learners. |
| Anonymous-first, upgradeable | Learners start with Firebase anonymous auth. Progress persists. Google Sign-In upgrade offered after session 3. |

---

## 2. System Architecture Overview

### 2.1 Three-Layer Stack

| Layer | Technology (MVP) |
|---|---|
| Content Layer | Approved lesson JSON files loaded from GCS (or local filesystem in dev). `outlines.yaml` + `concept_map.json` as course navigation. Optional Gemini context caching for cost reduction at scale. |
| Memory Layer | Firestore — per-learner mastery scores, FSRS params, session history |
| Mobile Frontend | Flutter — iOS + Android. Firebase Auth + Analytics + Crashlytics |

Cloud SQL and pgvector have been removed. There is no database for course content. Lesson content lives in pre-authored JSON files loaded into memory at backend startup. Learner state lives exclusively in Firestore.

### 2.2 Data Flow: One Session

1. Flutter authenticates the learner (Firebase Anonymous Auth). Retrieves or creates Firestore learner document.
2. App calls `POST /session/start` on Cloud Run, passing learner UID.
3. **Scheduler** (pure Python, no LLM) reads Firestore: fetches FSRS concept schedule, picks the next concept (earliest `next_review_at` past due, or lowest `mastery_score`). Returns `{lesson_id, difficulty_tier, module_character_id}` — zero network calls beyond the single Firestore read.
4. **LessonSession** starts a Gemini Flash chat with the lesson content injected from the in-memory JSON store. If `ENABLE_LESSON_CACHE=true`, the lesson's block cache handle is used as the prefix (shared across all learners).
5. App renders lesson text. Module character widget reacts to `character_emotion_state`.
6. **Quiz loop**: same Gemini chat session continues — LessonSession generates one question per turn, evaluates answers with full lesson context in window.
7. If learner answers incorrectly twice → **HelpSession** activates. Max 3 turns enforced in Python. Character switches to `helping` state.
8. After turn 3: if concept still unresolved → HelpSession outputs `gemini_handoff_prompt`. App surfaces Gemini referral card.
9. **SummaryCall** (single Gemini 3.1 Flash-Lite call, no history) writes timestamped session record and calls `run_fsrs()` to update concept mastery in Firestore.

---

## 3. Session Pipeline

### 3.1 Design Rationale: No Agent Framework

The original design used Google ADK with 4 `LlmAgent` classes. After review, ADK was removed for three reasons:

1. **ContextAgent was a pure-Python function pretending to be an LLM call.** Lesson scheduling (pick earliest `next_review_at`, fall back to lowest `mastery_score`) is deterministic. Using Gemini 2.5 Flash for this added ~600–1200 ms and cost with zero benefit.
2. **RAG was unnecessary.** The course is a fixed linear sequence. A learner always studies the next lesson on their schedule — there is no open-ended semantic search need. Embedding a concept ID and querying pgvector to retrieve the same static file every time was pure overhead (~400–600 ms per lesson turn).
3. **ADK marshalling overhead.** Each `LlmAgent` wraps the Gemini SDK in session service infrastructure. For a conversational learning session with a known, fixed sequence, direct `GenerativeModel.start_chat()` calls are simpler, faster, and easier to test.

The result: **pure-Python scheduler + 3 direct Gemini SDK calls per session**, down from 4 LLM calls via ADK.

### 3.2 Pipeline Components

| Component | Type | Model | Responsibility |
|---|---|---|---|
| `scheduler.pick_next_lesson()` | Pure Python | None | Read Firestore concept schedule. Return `{lesson_id, tier, module_character_id}`. No LLM. |
| `LessonSession` | `client.chats.create()` | Gemini 3.1 Flash-Lite, thinking=LOW (1024) | Deliver lesson. Run quiz loop. Track consecutive wrong answers. Emit `trigger_help`. |
| `HelpSession` | `client.chats.create()` | Gemini 3.1 Flash-Lite, thinking=MINIMAL (0) | Short clarification dialogue, hard-capped at 3 turns in Python. On unresolved exit: output `gemini_handoff_prompt`. |
| `SummaryCall` | Single `generate_content()` call | Gemini 3.1 Flash-Lite, thinking=MINIMAL (0) | Summarise session. Call `run_fsrs()`. Write Firestore session record + concept mastery. |

**Tools (plain async Python, no LLM):**

| Tool | Called by | Implementation |
|---|---|---|
| `run_fsrs(concept_id, ...)` | SummaryCall | Deterministic FSRS-4 update. Returns `{next_review_at, mastery_score, fsrs_stability, fsrs_difficulty}`. |

### 3.3 LessonSession: Teaching and Assessment in One Context Window

LessonSession handles the full interactive session from lesson delivery through quiz completion. Running both phases in one chat session is deliberate: the model that explained the concept is the same model that quizzes on it, with the full lesson text still in context. This produces coherent, targeted questions.

**LessonSession flow (multi-turn):**

```
Turn 1 (Teaching):
  System prompt contains: outlines.yaml + concept_map.json + approved lesson JSON for current lesson
  → Generates lesson narrative in module character's voice
  → Output: { lesson_text, character_emotion_state: "teaching", key_concepts[] }

Turn 2–N (Quiz, one question at a time):
  → Generates next quiz question (mc | tf | fill | command)
  → Output: { question_text, format, options[], character_emotion_state: "curious" }
  → Receives learner answer
  → Evaluates with full lesson context in window
  → Output: { correct: bool, explanation, concept_score_delta, character_emotion_state }
  → If 2nd consecutive wrong answer: add trigger_help: true
```

**Course navigation:** `outlines.yaml` and `concept_map.json` are always present in the system prompt (~10 K tokens). When a learner asks about a future concept, the model can correctly respond "that's covered in L09, Module 4" without any tool call or database lookup.

### 3.4 HelpSession State Machine

```
IDLE → (LessonSession emits trigger_help: true) → ACTIVE

ACTIVE → [Turn 1] Explains concept differently.
ACTIVE → [Turn 2] Asks a simpler version of the question.
ACTIVE → [Turn 3] Resolved or unresolved:
  - Resolved: { resolved: true, character_emotion_state: "celebrating" }
  - Unresolved: { resolved: false, gemini_handoff_prompt: "..." }

Turn cap enforced in Python: HelpSession.increment_turn() raises after turn 3.
```

> **HelpSession constraint:** "You have exactly 3 turns. You must resolve by turn 3. If unresolved at turn 3, output a `gemini_handoff_prompt`: a ready-to-use prompt string the learner can paste into Gemini to continue learning this concept in depth."

### 3.5 Gemini App Handoff (After Turn 3)

When HelpSession exits unresolved, the app surfaces a dismissible card:

> *"Still stuck? Keep learning this in Gemini →"*

Tapping opens the Gemini app via `url_launcher` with `gemini_handoff_prompt` pre-filled. The prompt is LLM-generated at resolution time — contextually accurate, not templated. `gemini_handoff_used` is tracked as a boolean in analytics only; the prompt content is never logged.

### 3.6 Character Emotion States

Every JSON response includes `character_emotion_state`. The Flutter app maps this to the active module character's PNG asset.

| State | Triggered by |
|---|---|
| `welcome` | Session start |
| `teaching` | Lesson delivery |
| `curious` | Question posed |
| `celebrating` | Correct answer |
| `encouraging` | First wrong answer |
| `helping` | HelpSession active |

---

## 4. Character Design: Module-Assigned Rotating Cast

### 4.1 The Memory Hook Model

Characters are **not chosen by the learner**. Each Linux module is permanently assigned one character. When a learner returns to a module via FSRS review, the same character reappears. The character is a retrieval cue — consistent environmental cues at encoding improve recall at retrieval.

| Module | Character |
|---|---|
| 1. What is Linux? | Tux Jr. — a friendly penguin cub |
| 2. The Terminal | Cursor — a sleek glowing robot |
| 3. Files & Directories | Filo — a paper-crane origami bird |
| 4. Working with Files | Snippy — a tiny scissors creature |
| 5. Users & Permissions | Keyra — a small guardian with a keyring |
| 6. Processes | Spinner — a fast whirling top character |
| 7. Networking Basics | Wavo — a wave/signal creature |
| 8. Package Management | Boxby — a cheerful box with arms |
| 9. Shell Scripting | Scrippy — a small scroll with a quill pen |

### 4.2 Image Technology: Static PNG with Flutter Cross-Fade

Characters are static PNGs generated by Gemini. Emotion transitions use Flutter's `AnimatedCrossFade` (300ms). All assets are bundled with the app — no network loading at runtime.

**Total asset count:** 9 characters × 6 emotions = 54 PNGs. Target under 80 KB each, total bundle under 4.5 MB.

### 4.3 Character Image Generation

#### Style Anchor Prompt (per character)
```
A [character description], cartoon illustration style, flat design,
bold clean outlines, soft pastel colour palette, neutral front-facing pose,
arms slightly out, friendly expression, 512x512px, transparent background,
no shadows, no gradients, consistent line weight throughout.
```

#### Emotion Variant Descriptors

| Emotion | Appended descriptor |
|---|---|
| `teaching` | `neutral attentive expression, one hand raised slightly, eyes open and engaged` |
| `curious` | `head tilted 15 degrees to the right, one eyebrow raised, slight smile` |
| `celebrating` | `both arms raised above head, wide open smile, slight forward lean` |
| `encouraging` | `one hand giving thumbs up, soft warm smile, slight head nod implied` |
| `helping` | `leaning forward 20 degrees, focused expression, one hand pointing forward` |
| `welcome` | `one hand waving, big friendly smile, slight bounce posture` |

All style anchor prompts are committed to `assets/characters/style_anchors.md`.

---

## 5. Content Layer

### 5.1 Course Structure

29 lessons across 9 modules. Full lesson outlines in `courses/linux-basics/outlines.yaml`. Concept relationships in `courses/linux-basics/concept_map.json`. Both files are the authoritative source of truth for content generation and runtime navigation.

### 5.2 Content Generation Pipeline (One-Time, at Authoring)

1. `generate_content.py` reads `outlines.yaml` + `concept_map.json` and calls Gemini to produce 3 difficulty-tier variants per lesson (Beginner / Intermediate / Advanced) plus 8 quiz questions per tier.
2. Human review: approved files moved to `courses/linux-basics/pipeline/approved/{tier}/L##.json`.
3. No embedding step. No database loading step. Approved JSON files are the terminal artefact.

**Pipeline output structure per approved file:**
```json
{
  "lesson_id": "L07",
  "tier": "beginner",
  "lesson": { "sections": [...], "key_takeaways": [...], "terminal_steps": [...] },
  "quiz": { "questions": [...] }
}
```

### 5.3 Runtime Content Loading

At Cloud Run startup (FastAPI lifespan), the backend loads all approved JSON files from `courses/linux-basics/pipeline/approved/` into an in-memory dict keyed by `(lesson_id, tier)`. On GCS-backed deployments, `GCS_PIPELINE_BUCKET` routes reads through `storage.py`'s `GcsBackend`. Local dev uses `LocalBackend` (no credentials needed).

`outlines.yaml` and `concept_map.json` (~10 K tokens total) are loaded once and injected into every LessonSession system prompt. No per-request file I/O.

### 5.4 Gemini Context Caching (Optional, Cost Optimisation)

**Disabled by default.** Enabled via `ENABLE_LESSON_CACHE=true` on Cloud Run when user volume justifies it.

**How it works:** Approved lesson content is grouped into blocks of ~10 lessons. At startup, `cache_manager.py` calls `CachedContent.create()` on Vertex AI for each block (~60 K tokens per block, 3 blocks for 29 lessons). Each LessonSession turn passes the block's cache handle — Gemini reuses the server-side KV state instead of reprocessing the prefix.

**Critical design property: caches are course-level, shared across all learners.** One cache handle per block serves every learner studying lessons in that block. User A and User B both on L07 reuse the identical cached KV state. The KV computation is paid once at cache creation; all downstream turns for all learners reuse it. At 100 learners doing 5 turns each, 500 Gemini calls share 3 cache creations.

**Block layout:**
- `block_0`: L01–L10 (all 3 tiers) ≈ 60 K tokens
- `block_1`: L11–L20 ≈ 60 K tokens
- `block_2`: L21–L29 ≈ 54 K tokens

**When disabled:** lesson content is injected as standard input tokens each turn. Functionally identical output; higher per-turn token cost; zero cache management complexity. Correct mode for local development.

**Enabling in production (zero-downtime):**
```bash
gcloud run services update agentic-learning-backend \
  --update-env-vars ENABLE_LESSON_CACHE=true
```

### 5.5 Firestore Schema (Memory Layer)

```
learners/{uid}
  — difficulty_tier, onboarding_complete

learners/{uid}/concepts/{concept_id}
  — mastery_score (0–1.0), fsrs_stability, fsrs_difficulty,
    last_review_at, next_review_at

learners/{uid}/sessions/{session_id}
  — lesson_id, tier_used, quiz_scores{}, time_on_task_seconds,
    help_triggered (bool), gemini_handoff_used (bool),
    summary_text, created_at, fsrs_result
```

No PII in Firestore. All data keyed by anonymous Firebase UID.

---

## 6. Flutter App Design

### 6.1 Architecture

| Component | Technology |
|---|---|
| Authentication | Firebase Auth — anonymous on first launch, Google Sign-In upgrade offered after session 3 |
| State management | Riverpod |
| Backend comms | Dart `http` package — REST to Cloud Run endpoint |
| Local cache | Hive — session state survives backgrounding |
| Character animations | Static PNGs with Flutter `AnimatedCrossFade` (300ms cross-fade on emotion state change) |
| Analytics | Firebase Analytics — events: `session_start`, `lesson_complete`, `quiz_answer`, `help_triggered`, `gemini_handoff_tapped`, `session_complete` |
| Crash reporting | Firebase Crashlytics |

### 6.2 Screen Flow

1. **Splash / Auth** — Anonymous sign-in. First time: brief onboarding (3 questions for difficulty tier assignment).
2. **Home / Dashboard** — Progress overview. Next lesson card with module character thumbnail. Streak counter.
3. **Session Screen** — Module character widget (top-right, 80×80dp). Lesson text card. Progress bar at top.
4. **Quiz Screen** — Question card. Format-appropriate tap-to-select input. Character reacts to each answer.
5. **Help Screen** — Slides up as bottom sheet. Character moves to center (120×120dp) during help turns.
6. **Gemini Referral Card** — Shown after unresolved turn 3. Non-blocking. Dismissible. Deep-links to Gemini app.
7. **Session Complete** — Summary card. Character in `celebrating` state. Next session teaser.

### 6.3 Character Widget Behaviour

- Position: top-right corner, 80×80dp, persistent Stack overlay across all session screens.
- Emotion change: `AnimatedCrossFade` (300ms) driven by `character_emotion_state` field from any backend response.
- During help: animates to center, scales to 120×120dp. Returns to corner on resolution.
- All PNG assets bundled — no network load at runtime.

---

## 7. Authentication & Learner Identity

1. Firebase creates an anonymous UID on first launch. All data linked to this UID immediately.
2. After session 3: non-blocking prompt to sign in with Google and save progress across devices.
3. Google Sign-In links the anonymous UID to the Google account. All history preserved. UID unchanged.
4. Uninstall before upgrading = new UID, orphaned history. Acceptable for MVP trial.

**Privacy:** Anonymous UIDs contain no PII. `gemini_handoff_used` tracked as boolean only — prompt content never logged.

---

## 8. Cost Model (100 Active Learners)

### Per-Session Token Budget (cache disabled)

| Component | Avg Tokens | Model | Cost |
|---|---|---|---|
| Scheduler (Firestore read) | — | None | ~$0.00 |
| LessonSession — teaching + quiz (multi-turn) | 1,750 in / 975 out | Gemini 3.1 Flash-Lite | ~$0.00015 (est.) |
| HelpSession (30% of sessions, blended) | 800 in / 500 out | Gemini 3.1 Flash-Lite | ~$0.00004 blended (est.) |
| SummaryCall + FSRS | 550 in / 320 out | Gemini 3.1 Flash-Lite | ~$0.00002 (est.) |
| **Total per session** | | | **~$0.00075** |

### Monthly Infrastructure (100 learners, 1 session/day = 3,000 sessions/month)

| Service | Cache disabled | Cache enabled |
|---|---|---|
| Gemini API | ~$2.25 | ~$0.80 (prefix tokens at 4× discount) |
| Gemini cache storage (3 blocks, 1hr TTL) | — | ~$0.01 |
| Cloud Run (scale-to-zero) | $0.00 | $0.00 |
| Firestore reads/writes | ~$0.09 | ~$0.09 |
| Firebase Auth / Analytics / Crashlytics | Free | Free |
| **Total** | **~$2.35/month** | **~$0.90/month** |

Cloud SQL removed: saves ~$7/month vs original architecture at all user counts.

At 1,000 active learners with cache enabled: ~$9/month. Healthy unit economics.

---

## 9. Development Phases

See `development_roadmap.md` for full task lists, PR breakdowns, and Definitions of Done.

| Phase | Title | Key deliverable |
|---|---|---|
| Phase 0 | GCP & Firebase Setup | All cloud services provisioned, IAM configured |
| Phase 1 | Content Generation | All 29 lessons × 3 tiers approved in `pipeline/approved/` |
| Phase 2 | Character Assets | 54 PNGs (9 characters × 6 emotions) bundled in Flutter |
| Phase 3 | Backend Simplification Refactor | ADK + RAG removed; scheduler, LessonSession, HelpSession, SummaryCall implemented; Cloud SQL decommissioned |
| Phase 4 | Integration & Load Testing | Full session flow tested end-to-end via Cloud Run |
| Phase 5 | Flutter App | All screens implemented; tested on iOS + Android |
| Phase 6 | Trial Launch | 20–50 trial learners on TestFlight + Play Internal Testing |

---

## 10. Open Decisions

| Decision | Status |
|---|---|
| App name / brand | Decide before Phase 6 (required for store listing) |
| Onboarding quiz design | 3 questions for difficulty tier assignment — design in Phase 5 |
| Gamification | Streak counter only for MVP. No XP system. |
| Push notifications | Add in Phase 6 if return rate data shows drop-off |
| BigQuery analytics stream | Add in Phase 6 before learner count grows past 100 |
| `ENABLE_LESSON_CACHE` activation threshold | Enable when monthly sessions exceed ~1,000 (break-even vs cache overhead) |

---

*Specification v1.1 — updated April 2026. Architecture simplified from ADK + pgvector (v1.0) to direct Gemini SDK + context window. See `notes/simplification-plan-remove-rag-adk.md` for full decision trail.*
