# Agentic Self-Paced Learning System
## Full Technical Specification — MVP v1.0
*Flutter · Google ADK · GCP-Native · Frugal Stack*
*February 2026*

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

### 2.1 Four-Layer Stack

| Layer | Technology (MVP) | Migration Path |
|---|---|---|
| Knowledge Layer | Cloud SQL for PostgreSQL + pgvector | AlloyDB AI with ScaNN index |
| Memory Layer | Firestore (per-learner mastery, FSRS params, session history) | Add BigQuery streaming export for analytics |
| Agent Orchestration | Google ADK on Cloud Run (scale-to-zero) | Vertex AI Agent Engine |
| Mobile Frontend | Flutter — iOS + Android. Firebase Auth + Analytics + Crashlytics | Same stack, add offline caching + push notifications |

> **On graph databases:** The concept prerequisite graph (25 nodes, ~40 edges) is fully representable as a `prerequisites[]` array in Cloud SQL plus concept-level mastery scores in Firestore. A graph database pays off at 100+ concepts across multiple courses. Revisit at Phase 3 when the catalogue grows.

### 2.2 Data Flow: One Session

1. Flutter authenticates the learner (Firebase Anonymous Auth). Retrieves or creates Firestore learner document.
2. App calls the ADK session endpoint on Cloud Run, passing learner UID.
3. **ContextAgent** reads Firestore: fetches FSRS-scheduled next concept(s), learner difficulty tier, last session summary. Determines which module character to display.
4. **RAGAgent** queries Cloud SQL + pgvector: retrieves content chunk for target concept at appropriate difficulty tier via cosine similarity.
5. **TutorAgent** generates lesson narrative from content chunks. Returns structured JSON: lesson text, character emotion state, suggested quiz questions.
6. App renders lesson. Module character widget reacts to emotion state.
7. **QuizAgent** generates 2–3 quiz questions. App renders them one at a time.
8. If learner answers incorrectly twice → **HelpAgent** triggers. Max 3 turns. Character switches to `helping` state.
9. After turn 3: if concept still unresolved → HelpAgent outputs a `gemini_handoff_prompt`. App surfaces Gemini referral card.
10. **SummaryAgent** writes timestamped session summary to Firestore. Updates concept mastery scores.
11. **SchedulerAgent** runs FSRS. Writes next review dates per concept to Firestore. Session complete.

---

## 3. Agent Architecture (Google ADK)

### 3.1 Design Rationale: Four Agents, Not Seven

The original design proposed seven agents. After review, three of those were demoted: **RAGAgent** was an LLM wrapper around a similarity search — that is a tool, not an agent. **QuizAgent** had no context about how the learner just experienced the lesson, producing questions generated cold from a concept tag alone. **SchedulerAgent** was already a deterministic Python function with no need for an LLM call.

Eliminating these as standalone agents and absorbing their responsibilities into the remaining agents produces a leaner, cheaper, and more coherent pipeline:

- `search_knowledge_base()` becomes a **tool** called by LessonAgent
- Quiz generation and answer evaluation become **phases** within LessonAgent's multi-turn session
- FSRS scheduling becomes a **tool** called by SummaryAgent

The result is **4 agents**, 2–3 fewer Gemini API calls per session, and a LessonAgent with full lesson context available when it generates and evaluates quiz questions.

### 3.2 Agent Pipeline

| Agent | Type | Model | Responsibility |
|---|---|---|---|
| ContextAgent | LlmAgent | Gemini 2.5 Flash-Lite | Read Firestore. Determine next concept, difficulty tier, session goal, and module character. Pass rich learner context to LessonAgent. |
| LessonAgent | Tool-calling LlmAgent | Gemini 2.0 Flash | Call `search_knowledge_base` tool to retrieve content chunks. Teach the concept. Generate and evaluate quiz questions. Drive character emotion states throughout. Signal HelpAgent trigger on 2nd wrong answer. |
| HelpAgent | LlmAgent (max 3 turns) | Gemini 2.0 Flash | Short clarification dialogue, hard-capped at 3 exchanges. On unresolved exit: outputs `gemini_handoff_prompt`. |
| SummaryAgent | Tool-calling LlmAgent | Gemini 2.5 Flash-Lite | Call `run_fsrs()` tool to compute next review dates. Write session summary and updated mastery scores to Firestore. |

**Tools (not agents):**

| Tool | Called By | Implementation |
|---|---|---|
| `search_knowledge_base(concept_id, tier)` | LessonAgent | Embeds query via `text-embedding-004`, runs pgvector cosine similarity search, returns top-3 content chunks |
| `run_fsrs(concept_id, mastery_score, outcome)` | SummaryAgent | Deterministic Python function. Computes updated `fsrs_stability`, `fsrs_difficulty`, `next_review_at` |

### 3.3 LessonAgent: Teaching and Assessment in One Context Window

LessonAgent handles the full interactive session between content delivery and quiz completion. Running both phases inside one agent and one context window is a deliberate design choice: the agent that explained the concept is the same agent that quizzes on it, with the full lesson text still in context. This produces more coherent, targeted questions and eliminates a JSON handoff between two agents.

**LessonAgent session flow (multi-turn):**

```
Turn 1 (Teaching):
  → Calls search_knowledge_base(concept_id, tier) — retrieves top-3 content chunks
  → Generates lesson narrative from chunks
  → Output: { lesson_text, character_emotion_state: "teaching", key_concepts[] }

Turn 2–N (Quiz, one question at a time):
  → Generates next quiz question (format selected from: mc | tf | fill | command)
  → Output: { question_text, format, options[], character_emotion_state: "curious" }

  → Receives learner answer
  → Evaluates against correct_answer with full lesson context in window
  → Output: { correct: bool, explanation, concept_score_delta, character_emotion_state: "celebrating"|"encouraging" }
  → If 2nd consecutive wrong answer on same concept: output { trigger_help: true }

Repeat quiz turns until 2–3 questions answered or help is triggered.
```

LessonAgent never calls HelpAgent directly. It emits `trigger_help: true` and the pipeline router activates HelpAgent. Control returns to LessonAgent after HelpAgent resolves.

### 3.4 HelpAgent State Machine

```
IDLE → (LessonAgent emits trigger_help: true) → ACTIVE

ACTIVE → [Turn 1] Agent explains concept differently. Learner responds.
ACTIVE → [Turn 2] Agent asks a simpler version of the question. Learner responds.
ACTIVE → [Turn 3] Resolved or unresolved:
  - Resolved: celebrate, resume LessonAgent flow.
  - Unresolved: mark concept for priority review, output gemini_handoff_prompt, surface Gemini referral card.

After Turn 3 → RESOLVED. Session always continues.
```

> **HelpAgent system prompt constraint:** "You have exactly 3 turns. You must resolve by turn 3. Do not ask open-ended questions. Do not go off-topic. If unresolved at turn 3, output a `gemini_handoff_prompt` field: a ready-to-use prompt string the learner can paste into Gemini to continue learning this concept in depth."

### 3.5 Gemini App Handoff (After Turn 3)

When the HelpAgent exits with an unresolved concept, the app surfaces a dismissible card:

> *"Still stuck? Keep learning this in Gemini →"*

Tapping opens the Gemini app (or gemini.google.com) via `url_launcher` with the `gemini_handoff_prompt` pre-filled. Example prompt generated by HelpAgent:

> *"I'm learning Linux basics and I'm struggling to understand file permissions and the chmod command. Can you explain it simply using a real-world analogy, then walk me through a practical example?"*

This is ~10 lines of Flutter code. The prompt is contextually accurate because it is LLM-generated at resolution time, not templated. No API cost. No session complexity added.

### 3.6 Character Emotion States

LessonAgent returns an `emotion_state` field in every JSON response throughout the session. The Flutter app maps this to the active module character's animation state.

| State | Triggered by | Animation behaviour |
|---|---|---|
| `welcome` | Session start | Friendly wave loop |
| `teaching` | Lesson delivery | Idle breathing loop, slight head nod |
| `curious` | Question posed to learner | Head tilt, raised eyebrow hold |
| `celebrating` | Correct answer | Jump + bounce, big smile |
| `encouraging` | First wrong answer | Gentle nod, thumbs up |
| `helping` | HelpAgent active (turns 1–3) | Lean forward, focused expression loop |

---

## 4. Character Design: Module-Assigned Rotating Cast

### 4.1 The Memory Hook Model

Characters are **not chosen by the learner**. Each Linux module is permanently assigned one character. The learner encounters different characters as they progress through modules. When they return to a module (via FSRS review), the same character reappears.

This is a deliberate memory encoding strategy: the character becomes a retrieval cue. Research on context-dependent memory shows that consistent environmental cues at encoding improve recall at retrieval. The character is that cue.

| Module | Assigned Character (example names) |
|---|---|
| 1. What is Linux? | Tux Jr. — a friendly penguin cub |
| 2. The Terminal | Cursor — a sleek glowing robot |
| 3. Files & Directories | Filo — a paper-crane origami bird |
| 4. Working with Files | Snippy — a tiny scissors creature |
| 5. Users & Permissions | Keyra — a small guardian with a keyring |
| 6. Processes | Spinner — a fast whirling top character |
| 7. Networking Basics | Wavo — a wave/signal creature |
| 8. Package Management | Boxby — a cheerful box with arms |

Character names and designs are illustrative. Final designs are generated and reviewed before development.

### 4.2 Image Technology: Static PNG with Flutter Cross-Fade

Characters are static PNGs generated by Gemini. Emotion transitions are handled by Flutter's `AnimatedCrossFade` widget (300ms). This is zero-cost, zero-tooling, and visually clean. Each character has one PNG per emotion state (6 images). All assets are bundled with the app — no network loading.

The Flutter character widget API uses the same `emotion_state` string field regardless of asset type, so if richer animation is ever desired in the future, assets can be swapped without changing any app logic.

**Total asset count:** 8 characters × 6 emotions = 48 PNGs. At 512×512px optimised for mobile, each is approximately 40–80 KB. Total bundle addition: under 4 MB.

### 4.3 Character Image Generation: Prompt Engineering Gemini

This section defines the process for producing a consistent, high-quality character library using Gemini image generation. Consistency across emotions and across sessions is critical — the memory hook breaks if the character looks different each time.

#### Step 1: Establish a Style Anchor Prompt

Before generating any emotion variants, generate a single canonical "neutral pose" image per character. This image is the style anchor — all subsequent prompts reference it. Use a fixed, detailed style description that does not change across any generation calls for that character.

**Style anchor template:**
```
A [character description], cartoon illustration style, flat design,
bold clean outlines, soft pastel colour palette, neutral front-facing pose,
arms slightly out, friendly expression, 512x512px, transparent background,
no shadows, no gradients, consistent line weight throughout.
```

**Example for "Cursor" (Module 2 — The Terminal):**
```
A small friendly robot with a blinking cursor on its chest screen,
cartoon illustration style, flat design, bold clean outlines,
soft blue and white colour palette, neutral front-facing pose,
arms slightly out, friendly expression, 512x512px, transparent background,
no shadows, no gradients, consistent line weight throughout.
```

Save this exact string. It is prepended to every emotion prompt for this character.

#### Step 2: Generate Emotion Variants

For each emotion, append a concise pose/expression descriptor to the style anchor prompt. Keep descriptors short — Gemini performs better with precise spatial instructions than abstract emotional words.

| Emotion | Appended descriptor |
|---|---|
| `teaching` | `neutral attentive expression, one hand raised slightly, eyes open and engaged` |
| `curious` | `head tilted 15 degrees to the right, one eyebrow raised, slight smile` |
| `celebrating` | `both arms raised above head, wide open smile, slight forward lean` |
| `encouraging` | `one hand giving thumbs up, soft warm smile, slight head nod implied` |
| `helping` | `leaning forward 20 degrees, focused expression, one hand pointing forward` |
| `welcome` | `one hand waving, big friendly smile, slight bounce posture` |

**Full prompt example for Cursor celebrating:**
```
A small friendly robot with a blinking cursor on its chest screen,
cartoon illustration style, flat design, bold clean outlines,
soft blue and white colour palette, 512x512px, transparent background,
no shadows, no gradients, consistent line weight throughout.
Both arms raised above head, wide open smile, slight forward lean.
```

#### Step 3: Consistency Checks and Iteration

After generating all 6 emotions for a character, review them side by side for:
- **Colour consistency:** same palette across all 6. Regenerate any outlier.
- **Proportion consistency:** head size, body ratio, limb length should not drift.
- **Line weight consistency:** outlines should feel visually identical across poses.
- **Background:** must be transparent (PNG). Reject any with white fill or soft edges.

If Gemini produces an inconsistent variant, re-run with the style anchor prompt and add: `"Match exactly the proportions, line weight, and colour palette of the character's neutral pose."` Iterate up to 3 times before accepting the closest result.

#### Step 4: Generation Order and Budget

Generate in this order to validate the process before committing to all 8 characters:

1. Generate all 6 emotions for **one character** (e.g. Cursor). Review carefully.
2. If satisfied, generate the remaining characters in module order.
3. Do not move to Step 5 (bundling) until all characters for Modules 1–3 are approved.

**Cost:** Gemini image generation costs approximately $0.039 per 1024×1024 image at current Vertex AI rates. 48 images = approximately **$1.87 total** for the full character library. Negligible.

#### Step 5: Export and Bundle

- Export each approved image as PNG with transparent background.
- Name files consistently: `{character_id}_{emotion}.png` (e.g. `cursor_celebrating.png`).
- Optimise with `pngquant` or `optipng` before bundling into Flutter assets.
- Declare all assets in `pubspec.yaml` under `flutter: assets:`.

---

## 5. Knowledge Base Design

### 5.1 Course structure

See the file located at `courses/linux-basics/course_structure_summary.md`

### 5.2 Content Generation Pipeline (One-Time, at Authoring)

1. You provide a lesson outline per concept: learning objectives (2–3 bullets) + example commands or scenarios.
2. Content pipeline calls Gemini 2.5 Flash to generate 3 difficulty tier variants per lesson: Beginner (heavy analogies), Intermediate (practical focus), Advanced (concise, system-level).
3. Pipeline generates up to 12 quiz questions per lesson per tier (3 per format: MC, TF, fill-in-the-blank, command completion). You review and approve.
4. Each content chunk is embedded using Vertex AI `text-embedding-004` and stored in Cloud SQL with pgvector.

**One-time generation cost estimate:** ~$0.20 for the full 25-lesson course.

### 5.3 Cloud SQL Schema (Knowledge Layer)

```sql
lessons (lesson_id, module_id, title, prerequisites[], concept_tags[])

content_chunks (chunk_id, lesson_id, tier ENUM[beginner|intermediate|advanced],
  content_text, embedding vector(768), token_count)

quiz_questions (question_id, lesson_id, tier, format ENUM[mc|tf|fill|command],
  question_text, correct_answer, distractors[], explanation)
```

### 5.4 Firestore Schema (Memory Layer)

```
learners/{uid}/profile
  — character_assignments{module_id: character_id}, difficulty_tier,
    onboarding_complete, created_at

learners/{uid}/concepts/{concept_id}
  — mastery_score (0–1.0), fsrs_stability, fsrs_difficulty,
    last_review_at, next_review_at, review_count

learners/{uid}/sessions/{session_id}
  — lesson_id, tier_used, quiz_scores{}, time_on_task_seconds,
    help_triggered (bool), gemini_handoff_used (bool),
    summary_text, created_at
```

> Note `gemini_handoff_used` is tracked per session. This gives you data on how often learners need to leave the system — a valuable signal for content quality.

---

## 6. Flutter App Design

### 6.1 Architecture

| Component | Technology |
|---|---|
| Authentication | Firebase Auth — anonymous on first launch, Google Sign-In upgrade offered after session 3 |
| State management | Riverpod |
| Backend comms | Dart `http` package — REST to Cloud Run ADK endpoint |
| Local cache | Hive — session state survives backgrounding |
| Character animations | Static PNGs with Flutter `AnimatedCrossFade` (300ms cross-fade on emotion state change) |
| Analytics | Firebase Analytics — events: `session_start`, `lesson_complete`, `quiz_answer`, `help_triggered`, `gemini_handoff_tapped`, `session_complete` |
| Crash reporting | Firebase Crashlytics |

### 6.2 Screen Flow

1. **Splash / Auth** — Anonymous sign-in. First time: brief onboarding (3 questions for difficulty tier assignment).
2. **Home / Dashboard** — Progress overview. Next lesson card with module character thumbnail. Streak counter.
3. **Session Screen** — Module character widget (top-right, 80×80dp). Lesson text card. Progress bar at top.
4. **Quiz Screen** — Question card. Format-appropriate input. Character reacts to each answer.
5. **Help Screen** — Slides up as bottom sheet. Character moves to center (120×120dp) during help turns. Dismisses after turn 3 or learner taps "Got it".
6. **Gemini Referral Card** — Shown after unresolved turn 3. Non-blocking. Dismissible. Deep-links to Gemini app with pre-filled prompt.
7. **Session Complete** — Summary card. XP earned. Character in `celebrating` state. Next session teaser.

### 6.3 Character Widget Behaviour

- **Position:** top-right corner, 80×80dp, persistent Stack overlay above all session screens.
- **Emotion change:** `AnimatedCrossFade` between PNG assets (300ms). Driven by `emotion_state` field from agent JSON.
- **During help:** animates to center, scales to 120×120dp. Returns to corner on resolution.
- **Character rotation:** ContextAgent returns `module_character_id` in session JSON. App loads the corresponding Rive asset for that session.
- **Assets bundled:** all character `.riv` files (or PNGs) shipped with the app. No network load.

---

## 7. Authentication & Learner Identity

1. Firebase creates an anonymous UID on first launch. All data linked to this UID immediately.
2. After session 3: non-blocking prompt to sign in with Google and save progress across devices.
3. Google Sign-In links the anonymous UID to the Google account. All history preserved. UID unchanged.
4. Uninstall before upgrading = new UID, orphaned history. Acceptable for MVP trial.

**Privacy:** Anonymous UIDs contain no PII. Track `gemini_handoff_used` as a boolean only — do not log the content of the handoff prompt in analytics.

---

## 8. Cost Model (100 Active Learners)

### Per-Session Token Budget

The 4-agent architecture eliminates RAGAgent and SchedulerAgent as standalone LLM calls, and absorbs QuizAgent into LessonAgent. This reduces the number of Gemini API calls per session from 6 to 4, saving the overhead of two separate prompt/response round trips. LessonAgent carries a larger context window than TutorAgent alone (it holds lesson content through quiz turns), but the net effect is a modest per-session cost reduction.

| Agent / Task | Avg Tokens | Model | Cost |
|---|---|---|---|
| ContextAgent | 400 in / 200 out | Flash-Lite | ~$0.00004 |
| LessonAgent — teaching + quiz (multi-turn) | 1,750 in / 975 out | 2.0 Flash | ~$0.00057 |
| HelpAgent (30% of sessions, blended) | 800 in / 500 out | 2.0 Flash | ~$0.00044 blended |
| SummaryAgent + FSRS tool call | 550 in / 320 out | Flash-Lite | ~$0.00006 |
| Embeddings (`search_knowledge_base` tool) | ~100 tokens | text-embedding-004 | ~$0.000001 |
| **Total per session** | | | **~$0.00051** |

> **Saving vs. original 7-agent design:** ~$0.00011/session (~18% reduction), primarily from eliminating the RAGAgent and QuizAgent as separate LLM calls. LessonAgent token cost is higher than TutorAgent alone due to carrying lesson context through quiz turns, but the two eliminated calls more than offset this.

### Monthly Infrastructure (100 learners, 1 session/day = 3,000 sessions/month)

| Service | Cost |
|---|---|
| Gemini API (all agents) | ~$1.53 |
| Cloud SQL db-f1-micro | ~$7.00 |
| Cloud Run (scale-to-zero) | ~$0.00 (free tier) |
| Firestore reads/writes | ~$0.09 (free tier covers this) |
| Firebase Auth / Analytics / Crashlytics | Free |
| **Total** | **~$8–11/month** |

At 1,000 active learners: ~$22–26/month. Healthy unit economics.

---

## 9. Development Roadmap

### Phase 1 — Foundation (Weeks 1–3)
- GCP + Firebase project setup. Cloud SQL, Firestore, Vertex AI, Cloud Run APIs enabled.
- Content pipeline: generate all 25 Linux lessons × 3 tiers using Gemini. Review and load into Cloud SQL with embeddings.
- Character style guide: generate reference PNGs for 3 characters × 6 emotions using Gemini. Brief Rive animator or prepare PNG fallback assets.
- ADK scaffold: ContextAgent + RAGAgent locally with ADK dev server. Wire to Cloud SQL and Firestore.

### Phase 2 — Core Agent Pipeline (Weeks 4–6)
- Build TutorAgent and QuizAgent. All 4 quiz formats. Test locally.
- Build HelpAgent with 3-turn state machine and `gemini_handoff_prompt` output. Test edge cases.
- Build SummaryAgent and SchedulerAgent (FSRS). Wire full pipeline end-to-end.
- Deploy to Cloud Run. IAM, secrets management, service account.

### Phase 3 — Flutter App (Weeks 7–9)
- App skeleton: Firebase Auth, Riverpod, navigation.
- Session Screen with Rive character widget (or PNG fallback) and emotion state driving.
- Quiz Screen (all 4 formats), Help bottom sheet, Gemini referral card, Session Complete screen.
- Wire app to Cloud Run. Full session flow test on iOS + Android simulators.
- Internal testing with 3–5 people.

### Phase 4 — Trial Launch (Week 10+)
- TestFlight (iOS) + Google Play Internal Testing (Android).
- 20–50 trial learners. Monitor session completion, quiz scores, help trigger rate, `gemini_handoff_used` rate, return rate.
- Iterate on content and character dialogue templates.
- Commission remaining 5 character Rive animations based on trial feedback.

---

## 10. Open Decisions (Pre-Phase 3)

| Decision | Recommendation |
|---|---|
| App name / brand | Decide before TestFlight. Needed for store listing. |
| Onboarding quiz design | 3 questions for difficulty tier assignment. Design in Phase 3. |
| Gamification | Streak counter only for MVP. No XP system yet. |
| Push notifications | Add in Phase 4 if return rate data shows drop-off. |
| BigQuery analytics stream | Add in Phase 4 before learner count grows past 100. |
| Full 8-character image set | Generate and review characters for Modules 1–3 first. Generate rest after trial validation. |

---

*Specification based on design sessions February 2026. Cost estimates use verified Vertex AI and GCP pricing as of February 2026.*
