#!/usr/bin/env python3
"""
generate_content.py — One-shot lesson + quiz generation pipeline for Linux Basics.

For each lesson × tier combination (29 lessons × 3 tiers = 87 total), sends a single
Gemini API request that produces both lesson content and quiz questions in one JSON
response, then writes the result to courses/linux-basics/pipeline/generated/.

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
import google.generativeai as genai
import yaml

from config import settings

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
OUTPUT_DIR = REPO_ROOT / "courses" / "linux-basics" / "pipeline" / "generated"

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
    lesson_prompt_template: str,
    quiz_prompt_template: str,
) -> str:
    """
    Build the full single-call prompt that asks Gemini for both lesson and quiz
    content in one JSON response.
    """
    context_json = json.dumps(context, indent=2)

    prompt = f"""\
You are generating structured educational content for a Linux basics mobile learning app.

You will produce BOTH lesson content AND quiz questions for a single lesson × tier combination
in one response. Return a single JSON object with exactly two top-level keys: "lesson" and "quiz".

=== LESSON GENERATION INSTRUCTIONS ===

{lesson_prompt_template}

=== QUIZ GENERATION INSTRUCTIONS ===

{quiz_prompt_template}

=== LESSON × TIER CONTEXT ===

The following compact context object defines this lesson and tier. Use ONLY the information
in this context — do not introduce concepts beyond what is listed in "concepts" and "examples"
for Beginner tier. The "generation_note" contains critical constraints; follow them exactly.
The "assumes" list tells you what the learner already knows from prior lessons.

```json
{context_json}
```

=== QUIZ PARAMETERS ===

question_count: {settings.question_count}
formats: {json.dumps(QUESTION_FORMATS)}

=== OUTPUT FORMAT ===

Return exactly one JSON object with this structure (no markdown fences, no text outside JSON):

{{
  "lesson": {{ ...lesson JSON matching the lesson_generation.md schema... }},
  "quiz": {{ ...quiz JSON matching the quiz_generation.md schema... }}
}}

The "lesson" object must have fields: lesson_id, title, tier, sections, key_takeaways, terminal_steps.
The "quiz" object must have fields: lesson_id, title, tier, questions.

Ensure lesson_id and tier in both objects match: lesson_id="{context['id']}", tier="{context['tier']}".
"""
    return prompt


# ---------------------------------------------------------------------------
# Gemini client
# ---------------------------------------------------------------------------

def configure_gemini() -> None:
    """Configure the google-generativeai SDK using Application Default Credentials."""
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/generative-language"]
    )
    request = google.auth.transport.requests.Request()
    credentials.refresh(request)
    genai.configure(credentials=credentials)


def get_model() -> genai.GenerativeModel:
    """Return a configured GenerativeModel instance."""
    return genai.GenerativeModel(
        model_name=settings.gemini_model,
        generation_config=genai.types.GenerationConfig(
            **{
                "temperature": settings.generation_temperature,
                "max_output_tokens": settings.generation_max_output_tokens,
                "response_mime_type": _RESPONSE_MIME_TYPE,
            }
        ),
    )


# ---------------------------------------------------------------------------
# Single generation unit
# ---------------------------------------------------------------------------

async def generate_one(
    lesson_outline: dict[str, Any],
    tier: str,
    concept_map: dict[str, Any],
    lesson_prompt_template: str,
    quiz_prompt_template: str,
    model: genai.GenerativeModel,
    semaphore: asyncio.Semaphore,
    output_dir: Path,
    dry_run: bool,
    resume: bool,
) -> tuple[str, str, str]:
    """
    Generate content for a single lesson × tier.

    Returns a tuple of (lesson_id, tier, status) where status is one of:
    "generated", "skipped", "failed".
    """
    lesson_id: str = lesson_outline["lesson_id"]
    tier_slug = TIER_FILENAME[tier]
    output_path = output_dir / f"{lesson_id}_{tier_slug}.json"
    label = f"[{lesson_id} {tier}]"

    # Resume: skip if output already exists
    if resume and output_path.exists():
        logger.info(f"{label} Skipped (already exists)")
        return lesson_id, tier, "skipped"

    if dry_run:
        logger.info(f"{label} Would generate -> {output_path.name}")
        return lesson_id, tier, "skipped"

    logger.info(f"{label} Generating...")
    start = time.monotonic()

    context = build_context(lesson_outline, concept_map, tier)
    prompt = build_prompt(context, lesson_prompt_template, quiz_prompt_template)

    async with semaphore:
        try:
            # google-generativeai does not have a native async API; run in executor
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: model.generate_content(prompt),
            )
            raw_text: str = response.text
        except Exception as exc:
            elapsed = time.monotonic() - start
            logger.error(f"{label} FAILED ({elapsed:.1f}s): {exc}")
            _write_error(output_path, f"API error: {exc}", "")
            return lesson_id, tier, "failed"

    # Parse the JSON response
    try:
        data: dict[str, Any] = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        elapsed = time.monotonic() - start
        logger.error(f"{label} FAILED ({elapsed:.1f}s): Invalid JSON — {exc}")
        _write_error(output_path, f"JSON decode error: {exc}", raw_text)
        return lesson_id, tier, "failed"

    # Validate top-level structure
    if "lesson" not in data or "quiz" not in data:
        elapsed = time.monotonic() - start
        msg = f'Response missing "lesson" or "quiz" key. Keys found: {list(data.keys())}'
        logger.error(f"{label} FAILED ({elapsed:.1f}s): {msg}")
        _write_error(output_path, msg, raw_text)
        return lesson_id, tier, "failed"

    # Build the final output document
    output: dict[str, Any] = {
        "lesson_id": lesson_id,
        "tier": tier,
        "lesson": data["lesson"],
        "quiz": data["quiz"],
    }

    try:
        with output_path.open("w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
    except OSError as exc:
        elapsed = time.monotonic() - start
        logger.error(f"{label} FAILED ({elapsed:.1f}s): Could not write output — {exc}")
        return lesson_id, tier, "failed"

    elapsed = time.monotonic() - start
    logger.info(f"{label} Done ({elapsed:.1f}s)")
    return lesson_id, tier, "generated"


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
    lesson_prompt_template: str,
    quiz_prompt_template: str,
    dry_run: bool,
    resume: bool,
) -> None:
    """Run the full generation pipeline with bounded concurrency."""
    if not dry_run:
        configure_gemini()
        model = get_model()
    else:
        model = None  # type: ignore[assignment]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    semaphore = asyncio.Semaphore(settings.concurrency_limit)

    tasks = []
    for lesson_outline in lessons:
        for tier in tiers:
            tasks.append(
                generate_one(
                    lesson_outline=lesson_outline,
                    tier=tier,
                    concept_map=concept_map,
                    lesson_prompt_template=lesson_prompt_template,
                    quiz_prompt_template=quiz_prompt_template,
                    model=model,
                    semaphore=semaphore,
                    output_dir=OUTPUT_DIR,
                    dry_run=dry_run,
                    resume=resume,
                )
            )

    results = await asyncio.gather(*tasks, return_exceptions=False)

    # Tally results
    counts: dict[str, int] = {"generated": 0, "skipped": 0, "failed": 0}
    for _lesson_id, _tier, status in results:
        counts[status] = counts.get(status, 0) + 1

    total = len(results)
    logger.info(
        f"\nSummary: {total} combinations — "
        f"{counts['generated']} generated, "
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
        help="Skip lesson × tier combinations where the output file already exists.",
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
        lesson_prompt_template = load_prompt(LESSON_PROMPT_PATH)
    except Exception as exc:
        logger.error(f"Failed to load lesson prompt from {LESSON_PROMPT_PATH}: {exc}")
        sys.exit(1)

    try:
        quiz_prompt_template = load_prompt(QUIZ_PROMPT_PATH)
    except Exception as exc:
        logger.error(f"Failed to load quiz prompt from {QUIZ_PROMPT_PATH}: {exc}")
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
        f"model={settings.gemini_model}, concurrency={settings.concurrency_limit}, mode={mode}"
    )
    if args.resume:
        logger.info("Resume mode: existing files will be skipped.")

    asyncio.run(
        run_pipeline(
            lessons=lessons,
            tiers=tiers,
            concept_map=concept_map,
            lesson_prompt_template=lesson_prompt_template,
            quiz_prompt_template=quiz_prompt_template,
            dry_run=args.dry_run,
            resume=args.resume,
        )
    )


if __name__ == "__main__":
    main()
