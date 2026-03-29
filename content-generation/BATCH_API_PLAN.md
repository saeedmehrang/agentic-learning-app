# Batch API Migration Plan for `generate_content.py`

Using the Gemini Batch API halves token costs (50% off input + output) at the cost of up to 24h
latency per batch. This plan adds a `--batch` flag to opt into the batch path while keeping the
existing online path intact for interactive use (e.g. `--lesson L04`).

---

## 1. Confirm model batch eligibility

Before any code changes, verify both models support batch on Vertex AI:

- `settings.gemini_model` (currently `gemini-3.1-flash-lite-preview`)
- `settings.reviewer_model` (currently `gemini-3-flash-preview`)

Check the [Vertex AI batch inference model list](https://cloud.google.com/vertex-ai/generative-ai/docs/multimodal/batch-prediction-gemini#supported_models).
Preview models may not be batch-eligible; we may need to fall back to a stable model ID for batch
runs.

Also confirm whether `ThinkingConfig` (used via `generation_thinking_level` /
`reviewer_thinking_level` in `config.py`) is honoured in batch mode. If not, both settings must be
set to `None` when running in batch mode.

---

## 2. New `--batch` CLI flag

In `parse_args()`:

```python
parser.add_argument(
    "--batch",
    action="store_true",
    help=(
        "Submit all requests as Gemini Batch API jobs (50%% cheaper, up to 24h latency). "
        "Incompatible with --dry-run. Not recommended with --lesson for a single lesson."
    ),
)
```

Guard in `main()`: raise an error if `--batch` and `--dry-run` are combined.

---

## 3. New `config.py` settings

Add to `ContentSettings`:

```python
# Batch API
batch_poll_interval_seconds: int = 60   # how often to poll for job completion
batch_display_name_prefix: str = "linux-basics-content-gen"
```

---

## 4. New helper: `submit_batch()`

```python
async def submit_batch(
    requests: list[dict[str, Any]],   # list of GenerateContentRequest dicts
    model: str,
    config: genai_types.GenerateContentConfig,
    display_name: str,
    client: genai.Client,
) -> Any:  # returns the batch job object
```

- Builds the inline request list: each entry has a `key` (e.g. `"L04-Beginner-gen"`) and a
  `request` with `contents` + `config`.
- Calls `client.batches.create(model=model, src=inline_requests, config={"display_name": display_name})`.
- Returns the job object (stores `.name` for later polling).

---

## 5. New helper: `poll_batch()`

```python
async def poll_batch(
    job_name: str,
    client: genai.Client,
    poll_interval: int,
) -> Any:  # returns the completed job object
```

Terminal states: `JOB_STATE_SUCCEEDED`, `JOB_STATE_FAILED`, `JOB_STATE_CANCELLED`,
`JOB_STATE_EXPIRED`.

- Loops with `await asyncio.sleep(poll_interval)` between `client.batches.get(name=job_name)`.
- Logs progress (state + elapsed time) on each poll.
- Raises on non-SUCCESS terminal states with full job info for debugging.

---

## 6. New helper: `parse_batch_results()`

```python
def parse_batch_results(
    job: Any,
) -> dict[str, dict[str, Any]]:  # key → parsed JSON response
```

- Reads from `job.dest.inlined_responses` (inline submission path; no GCS needed for ≤87 requests).
- For each response: if it has an error status, logs a warning and stores `None` for that key.
- Otherwise calls `json.loads(response.text)` and returns the keyed dict.

---

## 7. New top-level: `run_batch_pipeline()`

Replaces `run_pipeline()` when `--batch` is active. Three sequential batch phases:

### Phase 1 — Generation batch

1. Filter out lesson×tier pairs where `approved_path.exists()` (resume logic).
2. Build all prompts via existing `build_prompt()` — no change needed there.
3. Call `submit_batch()` with generation model + `generation_config()`.
4. Call `poll_batch()` until complete.
5. Parse results; write each to `OUTPUT_DIR/<tier>/<lesson_id>.json` (same as online path).
6. Collect failed keys for summary.

### Phase 2 — Review batch

1. For all successfully generated items, build review prompts (same logic as `call_reviewer()`).
2. Call `submit_batch()` with reviewer model + `reviewer_config()`.
3. Call `poll_batch()` until complete.
4. Parse results into `ReviewResult` objects via `ReviewResult.model_validate()` + `.compute_passed()`.
5. Write each review to `REVIEWED_DIR/<tier>/<lesson_id>_review.json`.

### Phase 3 — Conditional regeneration batch

1. Filter to items where `review_result.passed == False`.
2. If none, skip this phase entirely.
3. Build regen prompts via existing `call_regenerator()` logic (extract prompt-building into a
   separate `build_regen_prompt()` helper — see §8).
4. Call `submit_batch()` with generation model + `generation_config()`.
5. Call `poll_batch()` until complete.
6. Parse results.

### Phase 4 — Write approved files

- For passed reviews: write `raw_data` to `APPROVED_DIR`.
- For passed-after-regen: write regen output to `APPROVED_DIR`.
- Log final summary (same counts as online path).

---

## 8. Refactor: extract `build_regen_prompt()`

`call_regenerator()` currently mixes prompt construction with the API call. Extract the prompt
building into:

```python
def build_regen_prompt(
    original_generated: dict[str, Any],
    review_result: ReviewResult,
    context: dict[str, Any],
    combined_template: str,
    lesson_prompt_template: str,
    quiz_prompt_template: str,
) -> str:
```

This makes the regen prompt available to both the online `call_regenerator()` and the batch phase 3
without duplication. `call_regenerator()` becomes a thin wrapper calling `build_regen_prompt()` then
the API.

---

## 9. Key naming scheme for batch requests

Use a consistent key format so results can be matched back to lesson×tier pairs:

```
{lesson_id}-{tier_slug}-gen       # generation phase
{lesson_id}-{tier_slug}-review    # review phase
{lesson_id}-{tier_slug}-regen     # regeneration phase
```

---

## 10. `run_pipeline()` routing

Update `main()` to route based on the flag:

```python
if args.batch:
    asyncio.run(run_batch_pipeline(...))
else:
    asyncio.run(run_pipeline(...))   # existing online path, unchanged
```

---

## 11. Error handling differences from online path

| Scenario | Online path | Batch path |
|---|---|---|
| Single request API error | Caught per-task in `generate_one()` | Per-key error in `parse_batch_results()` |
| Job-level failure | N/A | `poll_batch()` raises; entire phase must be retried |
| Partial failures | Other tasks continue | Failed keys excluded from next phase; logged in summary |
| Resume | Checks `approved_path.exists()` before task | Same check before building Phase 1 request list |

---

## 12. Files changed / created

| File | Change |
|---|---|
| `generate_content.py` | Add `--batch` flag, `submit_batch()`, `poll_batch()`, `parse_batch_results()`, `run_batch_pipeline()`, extract `build_regen_prompt()` |
| `config.py` | Add `batch_poll_interval_seconds`, `batch_display_name_prefix` |
| `BATCH_API_PLAN.md` | This file (delete once implemented) |

No changes needed to: `review_models.py`, `embed_content.py`, `seed_db.py`, prompt files, outlines, or concept map.

---

## Open questions before implementing

1. Are `gemini-3.1-flash-lite-preview` and `gemini-3-flash-preview` listed as batch-eligible on
   Vertex AI? If not, which stable model IDs should be used for batch runs?
2. Does `ThinkingConfig` work in Vertex AI batch mode? If not, batch config must omit it.
3. Should the batch display name include a timestamp so multiple runs don't collide?
4. For 87 requests, inline submission is well under the 200k limit and 2GB cap — confirmed safe to
   use inline (no GCS upload needed).
