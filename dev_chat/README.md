# Dev Chat — Local Pipeline Test Interface

A Streamlit web app for interactively testing the full 4-agent pipeline
(ContextAgent → LessonAgent → HelpAgent → SummaryAgent) against the real
Cloud SQL and Firestore instances before the Flutter app is built.

**Why this exists:** Watching the pipeline run end-to-end in a browser reveals
the natural shape of the content — lesson text length, quiz question variety,
HelpAgent dialogue pacing, summary output — which directly informs Flutter UI
and UX decisions.

---

## Prerequisites

All commands run on the host machine (not the devcontainer). You need:

- `gcloud` CLI authenticated: `gcloud auth login`
- Application Default Credentials set up (one-time):
  ```bash
  gcloud auth application-default login
  gcloud auth application-default set-quota-project agentic-learning-app-e13cb
  ```
- `cloud-sql-proxy` installed ([download](https://cloud.google.com/sql/docs/postgres/sql-proxy))
- Python 3.11+ and `pip`

---

## Start-up sequence (3 terminals)

### Terminal 1 — Cloud SQL Auth Proxy

Fetch the connection name from Secret Manager (no local secrets needed) and
start the proxy:

```bash
CONNECTION_NAME=$(gcloud secrets versions access latest \
  --secret=DB_CONNECTION_NAME \
  --project=agentic-learning-app-e13cb)

cloud-sql-proxy "$CONNECTION_NAME" --port=5432
```

Leave this running. The backend connects to Cloud SQL through it.

### Terminal 2 — FastAPI backend

```bash
cd backend
uv run uvicorn main:app --host 0.0.0.0 --port 8080 --reload
```

Verify it's up:
```bash
curl http://localhost:8080/health
# → {"status":"ok"}
```

### Terminal 3 — Streamlit dev chat

```bash
cd dev_chat
pip install -r requirements.txt
streamlit run app.py
```

Open [localhost:8501](http://localhost:8501) in your browser.

---

## How to run a session

1. **Enter a learner UID** in the text field (default: `test-user-dev`) and click **Start Session**.
   - ContextAgent reads Firestore for this UID and picks the next concept.
   - New learner → picks L01 (beginner). Returning learner → picks next scheduled concept.

2. Click **📖 Load Lesson** — LessonAgent fetches content from Cloud SQL and delivers the lesson.

3. Click **🎯 Next Question** — LessonAgent generates a quiz question.
   - Multiple choice → radio buttons
   - True/False → two buttons
   - Fill-in-the-blank / command → radio buttons from options

4. Select your answer and submit. LessonAgent evaluates and shows feedback.

5. **Answer wrong twice on the same concept** → HelpAgent activates automatically.
   - You get a chat input to ask HelpAgent questions (3 turns max).
   - Turn 3 unresolved → a Gemini handoff card appears with a pre-filled prompt.

6. Click **✅ Finish Session** → SummaryAgent writes the session record and FSRS
   scores to Firestore.

7. Click **🔄 New Session** to start over (same or different UID).

---

## Sidebar — Session State Inspector

The left sidebar shows live state on every turn:

| Field | What it tells you |
|---|---|
| Phase badge | Current pipeline phase (LESSON / QUIZ / HELP / COMPLETE) |
| Concept / Tier / Character | ContextAgent output |
| Session Goal | What the agent decided to teach |
| Help turns remaining | 0–3 counter; turns red when ≤1 |
| Last Agent Response | Raw JSON expander for debugging |
| Backend status | Green = online, red = unreachable |

---

## Testing specific scenarios

**New learner flow:**
Use a UID that has no Firestore document. ContextAgent defaults to L01, beginner tier.

**Returning learner / FSRS scheduling:**
Complete a session with `test-user-dev`, then start a new session with the same UID.
ContextAgent should pick the next concept based on FSRS `next_review_at`.

**HelpAgent 3-turn cap:**
Answer incorrectly twice. In the help chat, send 3 messages without resolving.
Turn 3 exits with the Gemini handoff card.

**Different tiers:**
Create learner documents in Firestore with `difficulty_tier: intermediate` or `advanced`
to test content retrieval at different tiers. Currently only L01 and L02 are seeded
for all 3 tiers.

**Reset a learner:**
Delete `learners/test-user-dev` from the Firebase Console to wipe all progress
and test the new-learner path again.

---

## Passing a custom backend URL

```bash
BACKEND_URL=http://localhost:9090 streamlit run app.py
```

---

## What this is NOT

- Not a production UI — the session store is in-memory and resets on backend restart.
- Not the Flutter app — character assets are not shown, animations are not present.
- Not a load test — one session at a time is the intended usage.
