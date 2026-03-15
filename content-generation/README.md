# Content Generation Pipeline

One-shot lesson + quiz generation for the Linux Basics course using Gemini.

## Setup

```bash
uv venv content-generation/.venv
source content-generation/.venv/bin/activate
uv pip install -r content-generation/requirements.txt
```

Requires GCP Application Default Credentials (ADC) configured in the environment.

## Usage

```bash
python3 content-generation/generate_content.py [options]
```

| Flag | Description |
|---|---|
| _(none)_ | Generate all 87 combinations (29 lessons × 3 tiers) |
| `--lesson L04` | Generate only lesson L04 (all 3 tiers) |
| `--tier Beginner` | Generate only the Beginner tier (all 29 lessons) |
| `--lesson L04 --tier Beginner` | Generate exactly one combination |
| `--dry-run` | Print what would be generated without calling the API |
| `--resume` | Skip combinations where the output file already exists |

`--lesson` and `--tier` can be combined freely. `--resume` is safe to use on re-runs after partial failures.

## Configuration

Key constants at the top of `generate_content.py`:

| Constant | Value | Purpose |
|---|---|---|
| `GEMINI_MODEL` | `gemini-2.0-flash` | Model used for all generation calls |
| `CONCURRENCY_LIMIT` | `5` | Max simultaneous Gemini requests |
| `QUESTION_COUNT` | `8` | Quiz questions per lesson × tier |
| `QUESTION_FORMATS` | `[multiple_choice, true_false, fill_blank, command_completion]` | Quiz formats requested |

## Output

Files are written to `courses/linux-basics/pipeline/generated/` (gitignored):

- **`L04_beginner.json`** — generated content
- **`L04_beginner.error`** — created on failure; contains the error message and raw API response

Each output file has the shape:
```json
{
  "lesson_id": "L04",
  "tier": "Beginner",
  "lesson": { "sections": [], "key_takeaways": [], "terminal_steps": [] },
  "quiz":   { "questions": [] }
}
```

After review, approved files are moved to `courses/linux-basics/pipeline/approved/` before embedding.

## Source files

| File | Purpose |
|---|---|
| `courses/linux-basics/outlines.yaml` | Lesson titles, objectives, concepts, examples |
| `courses/linux-basics/concept_map.json` | Per-lesson `assumes[]`, `generation_note`, `cross_lesson_flag` |
| `courses/linux-basics/prompts/lesson_generation.md` | Lesson schema and quality rules sent to Gemini |
| `courses/linux-basics/prompts/quiz_generation.md` | Quiz schema and quality rules sent to Gemini |
