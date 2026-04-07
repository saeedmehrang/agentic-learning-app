# Dev Chat — Local Pipeline Test Interface

A Streamlit web app for interactively testing the full 4-agent pipeline 
(ContextAgent → LessonAgent → HelpAgent → SummaryAgent) against real 
Cloud SQL and Firestore instances.

**Why this exists:** It reveals the natural shape of the pipeline content—lesson length, quiz variety, HelpAgent dialogue, and summary output—informing Flutter UI and UX decisions before frontend development begins.

---

## Prerequisites

- **Python 3.13+** and **uv** installed.
- `gcloud` CLI authenticated: `gcloud auth login`.
- Application Default Credentials (ADC) configured:
  ```bash
  gcloud auth application-default login
  gcloud auth application-default set-quota-project agentic-learning-app-e13cb
  ```

  - `cloud-sql-proxy` installed ([installation guide](https://cloud.google.com/sql/docs/postgres/sql-proxy)).

-----

## Start-up Sequence (3 Terminals)

### Terminal 1 — Cloud SQL Auth Proxy

Fetch the connection name from Secret Manager and start the proxy on the default PostgreSQL port:

```bash
CONNECTION_NAME=$(gcloud secrets versions access latest \
  --secret=DB_CONNECTION_NAME \
  --project=agentic-learning-app-e13cb)

cloud-sql-proxy "$CONNECTION_NAME" --port=5432
```

### Terminal 2 — FastAPI Backend

Navigate to the backend directory and start the server:

```bash
cd backend
uv run uvicorn main:app --host 0.0.0.0 --port 8080 --reload
```

*Verify at `http://localhost:8080/health`*

### Terminal 3 — Streamlit Dev Chat

Navigate to the `dev_chat` directory, sync dependencies, and run the app:

```bash
cd dev_chat
uv sync
uv run streamlit run app.py
```

Open [localhost:3000](https://www.google.com/search?q=http://localhost:3000) in your browser.

-----

## How to Run a Session

1.  **Start Session:** Enter a **Learner UID** (e.g., `test-user-dev`). The ContextAgent will fetch/create the learner profile and determine the next concept based on FSRS logic.
2.  **📖 Load Lesson:** The LessonAgent retrieves content from Cloud SQL and delivers the teaching material.
3.  **🎯 Next Question:** Generates a quiz question. The UI adapts to the format (Multiple Choice, True/False, or Fill-in-the-blank).
4.  **Help Trigger:** If you answer incorrectly twice, the **HelpAgent** activates automatically. You have **3 turns** to resolve your confusion.
5.  **Gemini Handoff:** If the 3-turn limit is reached without resolution, a pre-filled Gemini prompt is generated for deeper 1-on-1 tutoring.
6.  **✅ Finish Session:** The SummaryAgent calculates scores, updates FSRS data in Firestore, and logs the session.

-----

## Sidebar — Session State Inspector

| Field | Description |
|---|---|
| **Phase Badge** | Tracks `LESSON`, `QUIZ`, `HELP`, or `COMPLETE`. |
| **Context Output** | Real-time metadata: Concept ID, Difficulty Tier, and Character. |
| **Help Turns** | Counter for HelpAgent interactions (turns red at $\leq 1$). |
| **Raw JSON** | Expandable view of the last agent's complete payload for debugging. |
| **Backend Health** | Live heartbeat check of the FastAPI server. |

-----

## Testing Scenarios

  * **New Learner:** Use a unique UID to trigger the "Beginner" (L01) path.
  * **Returning Learner:** Use an existing UID to test if FSRS correctly schedules the next concept.
  * **Tier Testing:** Manually edit a learner document in Firestore to `intermediate` or `advanced` to verify the LessonAgent pulls the correct difficulty tier.
  * **Reset:** Delete the learner document in the Firebase Console to wipe progress.

-----

## Configuration

To point the interface at a remote or different backend:

```bash
BACKEND_URL=http://your-ip:8080 uv run streamlit run app.py
```

```

### Quick Checks for your `app.py`:
1.  **Backend Health Check:** Your health check in `render_sidebar` uses `requests.get(f"{BACKEND_URL}/health")`. Ensure your FastAPI backend actually has a `@app.get("/health")` endpoint, or that will stay red.
2.  **Help Agent Response:** Your `_format_help_response` function currently returns a hardcoded string. Since your HelpAgent likely returns a `response_text` or similar field in the JSON, make sure to update that function to pull the actual string so you can see the agent's explanation!
```