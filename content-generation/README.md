# Content Generation Pipeline

End-to-end pipeline for generating, embedding, and seeding Linux Basics course content into Cloud SQL.

## Pipeline overview

```
outlines.yaml + concept_map.json
        │
        ▼
generate_content.py   →  courses/linux-basics/pipeline/generated/   (Gemini, 87 API calls)
        │
   [human review]
        │
        ▼
  pipeline/approved/
        │
        ▼
embed_content.py      →  courses/linux-basics/pipeline/embedded/    (Vertex AI text-embedding-005)
        │
        ▼
seed_db.py            →  Cloud SQL (via Cloud Run Job)
```

The `pipeline/` directory is gitignored and created at runtime.

---

## Scripts

| Script | Purpose |
|---|---|
| `generate_content.py` | Calls Gemini to generate lesson content + quiz questions for each lesson × tier |
| `embed_content.py` | Calls Vertex AI to produce 768-dim embeddings for approved lesson content |
| `seed_db.py` | Inserts embedded content (lessons, chunks, quiz questions) into Cloud SQL |
| `config.py` | Shared pydantic-settings config loaded from `../.env` |

---

## Setup

```bash
# For generate_content.py and embed_content.py
uv venv content-generation/.venv
source content-generation/.venv/bin/activate
uv pip install -r content-generation/requirements.txt
```

```bash
# For seed_db.py (local testing only — production runs as a Cloud Run Job)
uv pip install -r content-generation/requirements-seed.txt
```

Requires GCP Application Default Credentials (ADC) in the environment:
```bash
gcloud auth application-default login
```

---

## Step 1 — Generate content

```bash
python content-generation/generate_content.py [options]
```

| Flag | Description |
|---|---|
| _(none)_ | Generate all 87 combinations (29 lessons × 3 tiers) |
| `--lesson L04` | Generate only lesson L04 (all 3 tiers) |
| `--tier Beginner` | Generate only the Beginner tier (all 29 lessons) |
| `--lesson L04 --tier Beginner` | Generate exactly one combination |
| `--dry-run` | Print what would be generated without calling the API |
| `--resume` | Skip combinations where the output file already exists |

**Output:** `courses/linux-basics/pipeline/generated/L##_[tier].json`

On failure, a `.error` file is written alongside the expected output with the error message and raw API response.

Each output file shape:
```json
{
  "lesson_id": "L04",
  "tier": "Beginner",
  "lesson": { "sections": [], "key_takeaways": [], "terminal_steps": [] },
  "quiz":   { "questions": [] }
}
```

**After generation:** read all files in `pipeline/generated/`, then move approved ones to `pipeline/approved/`. Files needing changes can be regenerated with `--lesson L04 --tier Beginner`.

---

## Step 2 — Embed approved content

```bash
python content-generation/embed_content.py [options]
```

| Flag | Description |
|---|---|
| _(none)_ | Embed all files in `pipeline/approved/` |
| `--lesson L04` | Embed only lesson L04 (all tiers present in approved/) |
| `--tier Beginner` | Embed only Beginner tier files |
| `--dry-run` | Print what would be embedded without calling the API |
| `--resume` | Skip files already present in `pipeline/embedded/` |

Reads from `pipeline/approved/`, writes to `pipeline/embedded/`.

Each embedded file shape:
```json
{
  "lesson_id": "L04",
  "tier": "beginner",
  "chunk": {
    "text": "...concatenated lesson text...",
    "embedding": [0.012, -0.034, "...768 floats..."],
    "token_count": 412
  },
  "quiz_questions": ["...raw question objects..."],
  "lesson_metadata": { "title": "...", "key_takeaways": [], "terminal_steps": [] }
}
```

Note: `tier` is lowercased in the embedded output to match the Cloud SQL `difficulty_tier` ENUM.

---

## Step 3 — Seed Cloud SQL

Seeding runs as a **Cloud Run Job** in production. The job connects to Cloud SQL via the
Cloud SQL Python Connector (TLS tunnel through the Cloud SQL Admin API) — no VPC connector required.

### 3a. Provision infrastructure (one-time)

```bash
# Provision Artifact Registry repo (if not already done)
cd infra/terraform && terraform apply

# Enable pgvector extension (one-time, run from Cloud Shell if psql not available locally)
./infra/scripts/enable_pgvector.sh

# Apply Cloud SQL schema (one-time)
./infra/scripts/apply_schema.sh
```

`apply_schema.sh` applies `infra/sql/001_create_schema.sql` which creates:
- `lessons` table — lesson metadata (id, module, title, prerequisites, concept_tags)
- `content_chunks` table — lesson text + pgvector embedding (768-dim), with ivfflat index
- `quiz_questions` table — all 4 question formats, idempotent inserts

### 3b. Build and push the seed job image

Run from the **repo root** (Docker build context must include both `content-generation/` and `courses/`):

```bash
IMAGE=us-central1-docker.pkg.dev/agentic-learning-app-e13cb/agentic-learning/content-seed:latest

gcloud builds submit \
  --tag "$IMAGE" \
  --dockerfile content-generation/Dockerfile.seed \
  .
```

### 3c. Create the Cloud Run Job (one-time)

```bash
gcloud run jobs create content-seed \
  --image "$IMAGE" \
  --region us-central1 \
  --service-account cloud-run-app-identity@agentic-learning-app-e13cb.iam.gserviceaccount.com \
  --set-secrets DB_PASSWORD=DB_PASSWORD:latest,DB_INSTANCE_CONNECTION_NAME=DB_CONNECTION_NAME:latest \
  --memory 512Mi \
  --max-retries 1
```

### 3d. Execute the seed job

```bash
gcloud run jobs execute content-seed --region us-central1 --wait
```

All inserts are idempotent — safe to re-run after adding new approved content.

### Local dry-run (without Cloud SQL)

```bash
DB_PASSWORD=x DB_INSTANCE_CONNECTION_NAME=x \
  python content-generation/seed_db.py --dry-run
```

---

## Configuration

Settings are loaded from `../.env` via `config.py` (pydantic-settings, `extra="ignore"`).

| Setting | Default | Used by |
|---|---|---|
| `GEMINI_MODEL` | `gemini-2.0-flash` | `generate_content.py` |
| `GENERATION_TEMPERATURE` | `0.7` | `generate_content.py` |
| `GENERATION_MAX_OUTPUT_TOKENS` | `8192` | `generate_content.py` |
| `CONCURRENCY_LIMIT` | `5` | `generate_content.py` |
| `QUESTION_COUNT` | `8` | `generate_content.py` |
| `GCP_PROJECT_ID` | `agentic-learning-app-e13cb` | `embed_content.py` |
| `GCP_LOCATION` | `us-central1` | `embed_content.py` |
| `EMBEDDING_MODEL` | `text-embedding-005` | `embed_content.py` |
| `EMBEDDING_CONCURRENCY_LIMIT` | `5` | `embed_content.py` |
| `DB_INSTANCE_CONNECTION_NAME` | _(required in prod)_ | `seed_db.py` |
| `DB_PASSWORD` | _(required in prod)_ | `seed_db.py` |
| `DB_USER` | `app_user` | `seed_db.py` |
| `DB_NAME` | `learning_app` | `seed_db.py` |

---

## Source files

| File | Purpose |
|---|---|
| `courses/linux-basics/outlines.yaml` | Lesson titles, objectives, concepts, examples (29 lessons) |
| `courses/linux-basics/concept_map.json` | Per-lesson `introduces[]`, `assumes[]`, `generation_note`, `cross_lesson_flag` |
| `courses/linux-basics/prompts/lesson_generation.md` | Lesson schema and quality rules sent to Gemini |
| `courses/linux-basics/prompts/quiz_generation.md` | Quiz schema and quality rules sent to Gemini |

## Infra files

| File | Purpose |
|---|---|
| `infra/terraform/artifact_registry.tf` | Provisions the Docker image registry; grants Cloud Run SA reader access |
| `infra/sql/001_create_schema.sql` | Full DDL for the 3 content tables (idempotent, re-runnable) |
| `infra/scripts/apply_schema.sh` | Applies DDL to Cloud SQL via `gcloud sql connect` |
| `infra/scripts/enable_pgvector.sh` | Enables the pgvector extension (must run before `apply_schema.sh`) |
| `content-generation/Dockerfile.seed` | Cloud Run Job image for `seed_db.py` (build context = repo root) |
| `content-generation/requirements-seed.txt` | Dependencies for `seed_db.py` only (pg8000 + Cloud SQL Connector) |
