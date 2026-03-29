# Content Generation Pipeline

End-to-end pipeline for generating, embedding, and seeding Linux Basics course content into Cloud SQL.

## Pipeline overview

```
outlines.yaml + concept_map.json
        │
        ▼
generate_content.py   →  pipeline/generated/   (Gemini generation, up to 87 API calls)
        │
        ▼
   [auto review]       →  pipeline/reviewed/    (Gemini reviewer at temp=0.2, structured JSON)
        │
   blocking issues?
   yes → regenerate    →  pipeline/approved/    (fixed by second generation pass)
   no  ─────────────►  pipeline/approved/
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
| `generate_content.py` | Calls Gemini to generate, review, and (if needed) regenerate lesson + quiz content |
| `review_models.py` | Pydantic models for structured reviewer output (`ReviewResult`, issue types) |
| `embed_content.py` | Calls Vertex AI to produce 768-dim embeddings for approved lesson content |
| `seed_db.py` | Inserts embedded content (lessons, chunks, quiz questions) into Cloud SQL |
| `config.py` | Shared pydantic-settings config loaded from `../.env` |

---

## Setup

`pyproject.toml` is the single source of truth for dependencies. There are no `requirements*.txt` files — do not create them.

```bash
cd content-generation
uv venv .venv
source .venv/bin/activate

# generate_content.py and embed_content.py
uv pip install -e .

# seed_db.py also needs the Cloud SQL connector (local testing only)
uv pip install -e ".[seed]"

# tests (installs dev dependency-group via uv)
uv sync --group dev
```

Requires GCP Application Default Credentials (ADC) in the environment:
```bash
gcloud auth application-default login
```

The `generativelanguage.googleapis.com` API must be enabled on the GCP project (it is included in `infra/scripts/enable_apis.sh`). To enable it manually:
```bash
gcloud services enable generativelanguage.googleapis.com --project=agentic-learning-app-e13cb
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
| `--verbose` | Enable DEBUG logging, including per-call token usage printed to stdout |

**Output:** Up to three files per combination:

| Path | Purpose |
|---|---|
| `pipeline/generated/{tier}/L##.json` | Raw Gemini output |
| `pipeline/reviewed/{tier}/L##_review.json` | Reviewer's structured feedback (audit trail) |
| `pipeline/approved/{tier}/L##.json` | Final content — read by `embed_content.py` |

Where `{tier}` is one of `beginner`, `intermediate`, or `advanced`.

On failure, a `.error` file is written alongside the expected `generated/` path with the error message and raw API response.

Each approved file has the same shape as the generated file:
```json
{
  "lesson_id": "L04",
  "tier": "Beginner",
  "lesson": { "sections": [], "key_takeaways": [], "terminal_steps": [] },
  "quiz":   { "questions": [] }
}
```

Each review file shape:
```json
{
  "passed": true,
  "lesson_issues": [],
  "quiz_issues": [],
  "lesson_summary": "...",
  "quiz_summary": "..."
}
```

**Per combination, up to 3 LLM calls are made:** generate → review → regenerate (only if the reviewer found blocking issues). If review passes, `approved/` is written directly from the generated output.

### Pipeline logging

Every run writes to `courses/linux-basics/pipeline/pipeline_log.json` (created on first run). The file has two top-level sections:

- **`progress`** — one record per lesson × tier combination, updated in-place as each combination moves through phases. Useful for at-a-glance status after a crash or partial run.
- **`token_usage`** — append-only array of per-API-call token counts. Token counts come directly from the Gemini API response and are exact.

**Default behaviour (no `--verbose`):** each call is silently written to the log. A session summary is printed at INFO level at the end of the run:

```
Token usage this session (2 API calls: 1 generate, 1 review)
  prompt tokens:          3,412
  candidates tokens:      1,851
  thoughts tokens:          620
  total tokens:           5,883
  Logged to: .../courses/linux-basics/pipeline/pipeline_log.json
```

**With `--verbose`:** additionally prints one DEBUG line per call as it completes:

```
[pipeline_log] L01 Beginner (generate) — prompt=2100, candidates=1851, thoughts=420, total=4371
[pipeline_log] L01 Beginner (review)   — prompt=1312, candidates=480,  thoughts=200, total=1992
```

**Progress statuses:** `generating` → `generated` → `reviewing` → `reviewed` → [`regenerating` →] `approved` | `failed` | `skipped`

**Token usage fields:**

| Field | Description |
|---|---|
| `timestamp_utc` | ISO-8601 UTC timestamp of the call |
| `call_type` | `generate`, `review`, or `regenerate` |
| `lesson_id` | e.g. `L01` |
| `tier` | `Beginner`, `Intermediate`, or `Advanced` |
| `model` | Gemini model name used |
| `prompt_tokens` | Tokens in the input prompt |
| `candidates_tokens` | Tokens in the model's output |
| `thoughts_tokens` | Reasoning tokens (Gemini 3.x thinking mode only; 0 otherwise) |
| `total_tokens` | Sum of all token types for the call |

**`--resume` behaviour:** skips combinations where `pipeline/approved/` already exists. If a file exists in `pipeline/generated/` but not `pipeline/approved/`, the generation step is skipped and only the review + optional regen are re-run — useful for updating reviewer prompts without regenerating all 87 combinations.

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

Reads from `pipeline/approved/{tier}/`, writes to `pipeline/embedded/{tier}/`.

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
| `GEMINI_MODEL` | `gemini-3.1-flash-lite-preview` | `generate_content.py` |
| `GENERATION_TEMPERATURE` | `0.7` | `generate_content.py` |
| `GENERATION_MAX_OUTPUT_TOKENS` | `8192` | `generate_content.py` |
| `REVIEWER_MODEL` | `gemini-3-flash-preview` | `generate_content.py` |
| `REVIEWER_TEMPERATURE` | `0.2` | `generate_content.py` |
| `REVIEWER_MAX_OUTPUT_TOKENS` | `8192` | `generate_content.py` |
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
| `courses/linux-basics/prompts/combined_generation.md` | Combined generation prompt template with `{{TOKEN}}` placeholders — single source of truth |
| `courses/linux-basics/prompts/lesson_generation.md` | Lesson schema and quality rules (embedded into `combined_generation.md` at runtime) |
| `courses/linux-basics/prompts/quiz_generation.md` | Quiz schema and quality rules (embedded into `combined_generation.md` at runtime) |
| `courses/linux-basics/prompts/lesson_review.md` | Blocking review criteria for lesson content |
| `courses/linux-basics/prompts/quiz_review.md` | Blocking review criteria for quiz content |

## Infra files

| File | Purpose |
|---|---|
| `infra/terraform/artifact_registry.tf` | Provisions the Docker image registry; grants Cloud Run SA reader access |
| `infra/sql/001_create_schema.sql` | Full DDL for the 3 content tables (idempotent, re-runnable) |
| `infra/scripts/apply_schema.sh` | Applies DDL to Cloud SQL via `gcloud sql connect` |
| `infra/scripts/enable_pgvector.sh` | Enables the pgvector extension (must run before `apply_schema.sh`) |
| `content-generation/Dockerfile.seed` | Cloud Run Job image for `seed_db.py` (build context = repo root) |
| `content-generation/pyproject.toml` | Single source of truth for all dependencies; `[seed]` extras for `seed_db.py` |
