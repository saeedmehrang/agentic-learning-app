#!/usr/bin/env python3
"""
generate_content.py — One-shot lesson + quiz generation pipeline for Linux Basics.

For each lesson × tier combination (29 lessons × 3 tiers = 87 total), sends a single
Gemini API request that produces both lesson content and quiz questions in one JSON
response. Generated output is reviewed by a second Gemini call; if blocking issues are
found, a regeneration pass fixes them. Approved output is written to
courses/linux-basics/pipeline/approved/.

Usage:
    python generate_content.py [--lesson L04] [--tier Beginner] [--dry-run] [--resume]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

import google.auth
import google.auth.transport.requests
import google.genai as genai
import google.genai.types as genai_types
import yaml

from config import settings
from review_models import ReviewResult

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TIERS: list[str] = ["Beginner", "Intermediate", "Advanced"]
TIER_FILENAME: dict[str, str] = {
    "Beginner": "beginner",
    "Intermediate": "intermediate",
    "Advanced": "advanced",
}
TIER_INITIAL: dict[str, str] = {
    "Beginner": "B",
    "Intermediate": "I",
    "Advanced": "A",
}

_RESPONSE_MIME_TYPE = "application/json"
QUESTION_FORMATS = ["multiple_choice", "true_false", "fill_blank", "command_completion"]

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

OUTLINES_PATH = REPO_ROOT / "courses" / "linux-basics" / "outlines.yaml"
CONCEPT_MAP_PATH = REPO_ROOT / "courses" / "linux-basics" / "concept_map.json"
LESSON_PROMPT_PATH = (
    REPO_ROOT / "courses" / "linux-basics" / "prompts" / "lesson_generation.md"
)
QUIZ_PROMPT_PATH = (
    REPO_ROOT / "courses" / "linux-basics" / "prompts" / "quiz_generation.md"
)
COMBINED_PROMPT_PATH = (
    REPO_ROOT / "courses" / "linux-basics" / "prompts" / "combined_generation.md"
)
LESSON_REVIEW_PROMPT_PATH = (
    REPO_ROOT / "courses" / "linux-basics" / "prompts" / "lesson_review.md"
)
QUIZ_REVIEW_PROMPT_PATH = (
    REPO_ROOT / "courses" / "linux-basics" / "prompts" / "quiz_review.md"
)
OUTPUT_DIR = REPO_ROOT / "courses" / "linux-basics" / "pipeline" / "generated"
REVIEWED_DIR = REPO_ROOT / "courses" / "linux-basics" / "pipeline" / "reviewed"
APPROVED_DIR = REPO_ROOT / "courses" / "linux-basics" / "pipeline" / "approved"

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_outlines(path: Path) -> list[dict[str, Any]]:
    """Load lesson outlines from YAML. Returns a list of lesson dicts."""
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, list):
        raise ValueError(f"Expected a YAML list in {path}, got {type(data)}")
    return data


def load_concept_map(path: Path) -> dict[str, Any]:
    """Load the concept map JSON."""
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_prompt(path: Path) -> str:
    """Load a prompt template as a plain string."""
    with path.open("r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# Context object construction
# ---------------------------------------------------------------------------

def build_context(
    lesson_outline: dict[str, Any],
    concept_map: dict[str, Any],
    tier: str,
) -> dict[str, Any]:
    """Build the compact context object passed to Gemini for a single lesson × tier."""
    lesson_id: str = lesson_outline["lesson_id"]
    concept_map_lesson: dict[str, Any] = concept_map.get("lessons", {}).get(lesson_id, {})

    context: dict[str, Any] = {
        "id": lesson_id,
        "title": lesson_outline.get("title", ""),
        "tier": tier,
        "objectives": lesson_outline.get("learning_objectives", []),
        "concepts": lesson_outline.get("key_concepts", []),
        "examples": lesson_outline.get("example_commands_or_scenarios", []),
        "generation_note": concept_map_lesson.get("generation_note", ""),
        "assumes": concept_map_lesson.get("assumes", []),
    }

    # Include cross_lesson_flag if present — relevant for L11, L22, etc.
    cross_lesson_flag = concept_map_lesson.get("cross_lesson_flag")
    if cross_lesson_flag:
        context["cross_lesson_flag"] = cross_lesson_flag

    return context


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def build_prompt(
    context: dict[str, Any],
    combined_template: str,
    lesson_prompt_template: str,
    quiz_prompt_template: str,
) -> str:
    """Build the full generation prompt by injecting context into the combined template."""
    return (
        combined_template
        .replace("{{LESSON_GENERATION_INSTRUCTIONS}}", lesson_prompt_template)
        .replace("{{QUIZ_GENERATION_INSTRUCTIONS}}", quiz_prompt_template)
        .replace("{{CONTEXT_JSON}}", json.dumps(context, indent=2))
        .replace("{{QUESTION_COUNT}}", str(settings.question_count))
        .replace("{{QUESTION_FORMATS}}", json.dumps(QUESTION_FORMATS))
        .replace("{{LESSON_ID}}", context["id"])
        .replace("{{TIER}}", context["tier"])
    )


# ---------------------------------------------------------------------------
# Gemini clients
# ---------------------------------------------------------------------------

def make_client() -> genai.Client:
    """Return a google-genai Client using Application Default Credentials."""
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/generative-language"]
    )
    request = google.auth.transport.requests.Request()
    credentials.refresh(request)
    return genai.Client(credentials=credentials)


def _thinking_config(model: str, level: str | None) -> genai_types.ThinkingConfig | None:
    """Return ThinkingConfig only for Gemini 3 models; None otherwise."""
    if level is not None and model.startswith("gemini-3"):
        return genai_types.ThinkingConfig(thinking_level=level)
    return None


def generation_config() -> genai_types.GenerateContentConfig:
    """Return GenerateContentConfig for the generator model."""
    return genai_types.GenerateContentConfig(
        temperature=settings.generation_temperature,
        max_output_tokens=settings.generation_max_output_tokens,
        response_mime_type=_RESPONSE_MIME_TYPE,
        thinking_config=_thinking_config(settings.gemini_model, settings.generation_thinking_level),
    )


def reviewer_config() -> genai_types.GenerateContentConfig:
    """Return GenerateContentConfig for the reviewer model."""
    return genai_types.GenerateContentConfig(
        temperature=settings.reviewer_temperature,
        max_output_tokens=settings.reviewer_max_output_tokens,
        response_mime_type=_RESPONSE_MIME_TYPE,
        thinking_config=_thinking_config(settings.reviewer_model, settings.reviewer_thinking_level),
    )


# ---------------------------------------------------------------------------
# Reviewer and regenerator
# ---------------------------------------------------------------------------

async def call_reviewer(
    generated: dict[str, Any],
    context: dict[str, Any],
    lesson_review_template: str,
    quiz_review_template: str,
    client: genai.Client,
    semaphore: asyncio.Semaphore,
) -> ReviewResult:
    """Call the reviewer LLM and return a structured ReviewResult."""
    review_prompt = f"""\
{lesson_review_template}

{quiz_review_template}

=== ORIGINAL LESSON CONTEXT ===

```json
{json.dumps(context, indent=2)}
```

=== GENERATED CONTENT TO REVIEW ===

```json
{json.dumps(generated, indent=2)}
```

=== OUTPUT FORMAT ===

Return a single JSON object with exactly these top-level keys:
- "lesson_issues": array of lesson issue objects (may be empty)
- "quiz_issues": array of quiz issue objects (may be empty)
- "lesson_summary": string (one sentence overall assessment)
- "quiz_summary": string (one sentence overall assessment)

Do not include a "passed" field — it will be computed from your issues.
"""
    async with semaphore:
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: client.models.generate_content(
                model=settings.reviewer_model,
                contents=review_prompt,
                config=reviewer_config(),
            ),
        )
        raw_text: str = response.text

    data = json.loads(raw_text)
    result = ReviewResult.model_validate(data)
    result.compute_passed()
    return result


async def call_regenerator(
    original_generated: dict[str, Any],
    review_result: ReviewResult,
    context: dict[str, Any],
    combined_template: str,
    lesson_prompt_template: str,
    quiz_prompt_template: str,
    client: genai.Client,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    """Regenerate content incorporating reviewer feedback on blocking issues."""
    blocking_lesson = [
        i.model_dump() for i in review_result.lesson_issues if i.severity == "blocking"
    ]
    blocking_quiz = [
        i.model_dump() for i in review_result.quiz_issues if i.severity == "blocking"
    ]

    revision_section = f"""
=== REVISION INSTRUCTIONS ===

A previous generation of this content had quality issues. Fix ONLY the blocking issues
listed below. Preserve all field values not mentioned in the issues exactly as-is.
Do not restructure or rewrite sections that are not flagged.

LESSON ISSUES TO FIX:
{json.dumps(blocking_lesson, indent=2)}

QUIZ ISSUES TO FIX:
{json.dumps(blocking_quiz, indent=2)}

PREVIOUS GENERATION (for reference):
```json
{json.dumps(original_generated, indent=2)}
```
"""
    base_prompt = build_prompt(context, combined_template, lesson_prompt_template, quiz_prompt_template)
    regen_prompt = base_prompt + revision_section

    async with semaphore:
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: client.models.generate_content(
                model=settings.gemini_model,
                contents=regen_prompt,
                config=generation_config(),
            ),
        )
        raw_text: str = response.text

    return json.loads(raw_text)


# ---------------------------------------------------------------------------
# Single generation unit
# ---------------------------------------------------------------------------

async def generate_one(
    lesson_outline: dict[str, Any],
    tier: str,
    concept_map: dict[str, Any],
    combined_template: str,
    lesson_prompt_template: str,
    quiz_prompt_template: str,
    lesson_review_template: str,
    quiz_review_template: str,
    client: genai.Client,
    semaphore: asyncio.Semaphore,
    dry_run: bool,
    resume: bool,
) -> tuple[str, str, str]:
    """
    Generate, review, and optionally regenerate content for a single lesson × tier.

    Returns (lesson_id, tier, status) where status is one of:
    "approved", "approved_after_regen", "skipped", "failed".
    """
    lesson_id: str = lesson_outline["lesson_id"]
    tier_slug = TIER_FILENAME[tier]
    generated_path = OUTPUT_DIR / tier_slug / f"{lesson_id}.json"
    reviewed_path = REVIEWED_DIR / tier_slug / f"{lesson_id}_review.json"
    approved_path = APPROVED_DIR / tier_slug / f"{lesson_id}.json"
    label = f"[{lesson_id} {tier}]"

    # Resume: skip if approved output already exists
    if resume and approved_path.exists():
        logger.info(f"{label} Skipped (already approved)")
        return lesson_id, tier, "skipped"

    if dry_run:
        logger.info(f"{label} Would generate -> {tier_slug}/{approved_path.name}")
        return lesson_id, tier, "skipped"

    logger.info(f"{label} Generating...")
    start = time.monotonic()

    context = build_context(lesson_outline, concept_map, tier)

    # --- Phase 1: Generate (or load from generated/ if partial resume) ---
    if resume and generated_path.exists():
        logger.info(f"{label} Loading existing generated content...")
        try:
            raw_data: dict[str, Any] = json.loads(generated_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.error(f"{label} Failed to load generated file: {exc}")
            return lesson_id, tier, "failed"
    else:
        prompt = build_prompt(context, combined_template, lesson_prompt_template, quiz_prompt_template)
        async with semaphore:
            try:
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda: client.models.generate_content(
                        model=settings.gemini_model,
                        contents=prompt,
                        config=generation_config(),
                    ),
                )
                raw_text: str = response.text
            except Exception as exc:
                elapsed = time.monotonic() - start
                logger.error(f"{label} FAILED ({elapsed:.1f}s): {exc}")
                _write_error(generated_path, f"API error: {exc}", "")
                return lesson_id, tier, "failed"

        try:
            raw_data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            elapsed = time.monotonic() - start
            logger.error(f"{label} FAILED ({elapsed:.1f}s): Invalid JSON — {exc}")
            _write_error(generated_path, f"JSON decode error: {exc}", raw_text)
            return lesson_id, tier, "failed"

        if "lesson" not in raw_data or "quiz" not in raw_data:
            elapsed = time.monotonic() - start
            msg = f'Response missing "lesson" or "quiz" key. Keys found: {list(raw_data.keys())}'
            logger.error(f"{label} FAILED ({elapsed:.1f}s): {msg}")
            _write_error(generated_path, msg, raw_text)
            return lesson_id, tier, "failed"

        generated_output: dict[str, Any] = {
            "lesson_id": lesson_id,
            "tier": tier,
            "lesson": raw_data["lesson"],
            "quiz": raw_data["quiz"],
        }
        try:
            with generated_path.open("w", encoding="utf-8") as f:
                json.dump(generated_output, f, indent=2, ensure_ascii=False)
        except OSError as exc:
            elapsed = time.monotonic() - start
            logger.error(f"{label} FAILED ({elapsed:.1f}s): Could not write generated file — {exc}")
            return lesson_id, tier, "failed"
        raw_data = generated_output

    # --- Phase 2: Review ---
    logger.info(f"{label} Reviewing...")
    try:
        review_result = await call_reviewer(
            generated=raw_data,
            context=context,
            lesson_review_template=lesson_review_template,
            quiz_review_template=quiz_review_template,
            client=client,
            semaphore=semaphore,
        )
    except Exception as exc:
        elapsed = time.monotonic() - start
        logger.error(f"{label} Review FAILED ({elapsed:.1f}s): {exc}")
        return lesson_id, tier, "failed"

    try:
        with reviewed_path.open("w", encoding="utf-8") as f:
            json.dump(review_result.model_dump(), f, indent=2, ensure_ascii=False)
    except OSError as exc:
        logger.warning(f"{label} Could not write review file: {exc}")

    # --- Phase 3: Conditional regeneration ---
    if review_result.passed:
        final = raw_data
        status = "approved"
        logger.info(f"{label} Review passed.")
    else:
        blocking_count = sum(
            1 for i in review_result.lesson_issues + review_result.quiz_issues
            if i.severity == "blocking"
        )
        logger.info(f"{label} Review found {blocking_count} blocking issue(s). Regenerating...")
        try:
            regen_data = await call_regenerator(
                original_generated=raw_data,
                review_result=review_result,
                context=context,
                combined_template=combined_template,
                lesson_prompt_template=lesson_prompt_template,
                quiz_prompt_template=quiz_prompt_template,
                client=client,
                semaphore=semaphore,
            )
        except Exception as exc:
            elapsed = time.monotonic() - start
            logger.error(f"{label} Regen FAILED ({elapsed:.1f}s): {exc}")
            return lesson_id, tier, "failed"

        if "lesson" not in regen_data or "quiz" not in regen_data:
            elapsed = time.monotonic() - start
            logger.error(f"{label} FAILED ({elapsed:.1f}s): Regen missing lesson/quiz keys")
            return lesson_id, tier, "failed"

        final = {
            "lesson_id": lesson_id,
            "tier": tier,
            "lesson": regen_data["lesson"],
            "quiz": regen_data["quiz"],
        }
        status = "approved_after_regen"

    # --- Phase 4: Write approved output ---
    try:
        with approved_path.open("w", encoding="utf-8") as f:
            json.dump(final, f, indent=2, ensure_ascii=False)
    except OSError as exc:
        elapsed = time.monotonic() - start
        logger.error(f"{label} FAILED ({elapsed:.1f}s): Could not write approved file — {exc}")
        return lesson_id, tier, "failed"

    elapsed = time.monotonic() - start
    logger.info(f"{label} Done ({elapsed:.1f}s) — {status}")
    return lesson_id, tier, status


def _write_error(output_path: Path, message: str, raw_response: str) -> None:
    """Write an error file alongside the expected output path."""
    error_path = output_path.with_suffix(".error")
    try:
        with error_path.open("w", encoding="utf-8") as f:
            f.write(f"Error: {message}\n\n")
            if raw_response:
                f.write("=== Raw API Response ===\n")
                f.write(raw_response)
    except OSError:
        pass  # Best-effort; don't mask the original error


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

async def run_pipeline(
    lessons: list[dict[str, Any]],
    tiers: list[str],
    concept_map: dict[str, Any],
    combined_template: str,
    lesson_prompt_template: str,
    quiz_prompt_template: str,
    lesson_review_template: str,
    quiz_review_template: str,
    dry_run: bool,
    resume: bool,
) -> None:
    """Run the full generation pipeline with bounded concurrency."""
    if not dry_run:
        client = make_client()
    else:
        client = None  # type: ignore[assignment]

    for tier_slug in TIER_FILENAME.values():
        (OUTPUT_DIR / tier_slug).mkdir(parents=True, exist_ok=True)
        (REVIEWED_DIR / tier_slug).mkdir(parents=True, exist_ok=True)
        (APPROVED_DIR / tier_slug).mkdir(parents=True, exist_ok=True)

    semaphore = asyncio.Semaphore(settings.concurrency_limit)

    tasks = []
    for lesson_outline in lessons:
        for tier in tiers:
            tasks.append(
                generate_one(
                    lesson_outline=lesson_outline,
                    tier=tier,
                    concept_map=concept_map,
                    combined_template=combined_template,
                    lesson_prompt_template=lesson_prompt_template,
                    quiz_prompt_template=quiz_prompt_template,
                    lesson_review_template=lesson_review_template,
                    quiz_review_template=quiz_review_template,
                    client=client,
                    semaphore=semaphore,
                    dry_run=dry_run,
                    resume=resume,
                )
            )

    results = await asyncio.gather(*tasks, return_exceptions=False)

    # Tally results
    counts: dict[str, int] = {"approved": 0, "approved_after_regen": 0, "skipped": 0, "failed": 0}
    for _lesson_id, _tier, status in results:
        counts[status] = counts.get(status, 0) + 1

    total = len(results)
    logger.info(
        f"\nSummary: {total} combinations — "
        f"{counts['approved']} approved, "
        f"{counts['approved_after_regen']} approved_after_regen, "
        f"{counts['skipped']} skipped, "
        f"{counts['failed']} failed."
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate Linux Basics lesson and quiz content via Gemini."
    )
    parser.add_argument(
        "--lesson",
        metavar="L04",
        help="Generate only the specified lesson ID (e.g. L04). All tiers unless --tier given.",
    )
    parser.add_argument(
        "--tier",
        choices=TIERS,
        metavar="Beginner|Intermediate|Advanced",
        help="Generate only the specified tier. All lessons unless --lesson given.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be generated without calling the API.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip lesson × tier combinations where the approved output file already exists.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Load source data
    try:
        all_outlines = load_outlines(OUTLINES_PATH)
    except Exception as exc:
        logger.error(f"Failed to load outlines from {OUTLINES_PATH}: {exc}")
        sys.exit(1)

    try:
        concept_map = load_concept_map(CONCEPT_MAP_PATH)
    except Exception as exc:
        logger.error(f"Failed to load concept map from {CONCEPT_MAP_PATH}: {exc}")
        sys.exit(1)

    try:
        combined_template = load_prompt(COMBINED_PROMPT_PATH)
    except Exception as exc:
        logger.error(f"Failed to load combined prompt from {COMBINED_PROMPT_PATH}: {exc}")
        sys.exit(1)

    try:
        lesson_prompt_template = load_prompt(LESSON_PROMPT_PATH)
    except Exception as exc:
        logger.error(f"Failed to load lesson prompt from {LESSON_PROMPT_PATH}: {exc}")
        sys.exit(1)

    try:
        quiz_prompt_template = load_prompt(QUIZ_PROMPT_PATH)
    except Exception as exc:
        logger.error(f"Failed to load quiz prompt from {QUIZ_PROMPT_PATH}: {exc}")
        sys.exit(1)

    try:
        lesson_review_template = load_prompt(LESSON_REVIEW_PROMPT_PATH)
    except Exception as exc:
        logger.error(f"Failed to load lesson review prompt from {LESSON_REVIEW_PROMPT_PATH}: {exc}")
        sys.exit(1)

    try:
        quiz_review_template = load_prompt(QUIZ_REVIEW_PROMPT_PATH)
    except Exception as exc:
        logger.error(f"Failed to load quiz review prompt from {QUIZ_REVIEW_PROMPT_PATH}: {exc}")
        sys.exit(1)

    # Filter lessons
    lessons = all_outlines
    if args.lesson:
        lesson_id_upper = args.lesson.upper()
        lessons = [l for l in all_outlines if l["lesson_id"] == lesson_id_upper]
        if not lessons:
            valid_ids = [l["lesson_id"] for l in all_outlines]
            logger.error(
                f"Lesson '{args.lesson}' not found. Valid IDs: {', '.join(valid_ids)}"
            )
            sys.exit(1)

    # Filter tiers
    tiers = TIERS
    if args.tier:
        tiers = [args.tier]

    total = len(lessons) * len(tiers)
    mode = "dry-run" if args.dry_run else "live"
    logger.info(
        f"Content generation pipeline starting — {total} combination(s), "
        f"model={settings.gemini_model}, reviewer={settings.reviewer_model}, "
        f"concurrency={settings.concurrency_limit}, mode={mode}"
    )
    if args.resume:
        logger.info("Resume mode: existing approved files will be skipped.")

    asyncio.run(
        run_pipeline(
            lessons=lessons,
            tiers=tiers,
            concept_map=concept_map,
            combined_template=combined_template,
            lesson_prompt_template=lesson_prompt_template,
            quiz_prompt_template=quiz_prompt_template,
            lesson_review_template=lesson_review_template,
            quiz_review_template=quiz_review_template,
            dry_run=args.dry_run,
            resume=args.resume,
        )
    )


if __name__ == "__main__":
    main()
