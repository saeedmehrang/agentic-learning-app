#!/usr/bin/env python3
"""
embed_content.py — Embedding pipeline for approved Linux Basics lesson content.

For each approved lesson × tier JSON in courses/linux-basics/pipeline/approved/{tier}/,
calls Vertex AI text-embedding-005 to produce a 768-dim embedding of the lesson text,
then writes an enriched JSON file to courses/linux-basics/pipeline/embedded/{tier}/.

The embedded JSON contains the lesson text, its embedding vector, and the raw quiz
questions — everything seed_db.py needs to populate Cloud SQL.

Usage:
    python embed_content.py [--lesson L04] [--tier Beginner] [--dry-run] [--resume]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

import vertexai
from vertexai.language_models import TextEmbeddingInput, TextEmbeddingModel

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

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

APPROVED_DIR = REPO_ROOT / "courses" / "linux-basics" / "pipeline" / "approved"
EMBEDDED_DIR = REPO_ROOT / "courses" / "linux-basics" / "pipeline" / "embedded"

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
# Text extraction
# ---------------------------------------------------------------------------


def extract_chunk_text(lesson: dict[str, Any]) -> str:
    """
    Build the plain-text string to embed for a lesson.

    Concatenates section headings + bodies, key takeaways, and terminal step
    descriptions. One string per lesson × tier — maps to one content_chunk row.
    """
    parts: list[str] = []

    for section in lesson.get("sections", []):
        heading = section.get("heading", "").strip()
        body = section.get("body", "").strip()
        if heading:
            parts.append(heading)
        if body:
            parts.append(body)

    takeaways = lesson.get("key_takeaways", [])
    if takeaways:
        parts.append("Key takeaways: " + " | ".join(takeaways))

    for step in lesson.get("terminal_steps", []):
        prompt = step.get("prompt", "").strip()
        command = step.get("command", "").strip()
        expected = step.get("expected_output", "").strip()
        if prompt:
            parts.append(prompt)
        if command:
            parts.append(f"Command: {command}")
        if expected:
            parts.append(f"Output: {expected}")

    return "\n\n".join(parts)


def estimate_token_count(text: str) -> int:
    """Approximate token count as word count. Vertex AI does not return token counts for embeddings."""
    return len(text.split())


# ---------------------------------------------------------------------------
# Vertex AI client
# ---------------------------------------------------------------------------


def configure_vertexai() -> None:
    """Initialise the Vertex AI SDK using Application Default Credentials."""
    vertexai.init(project=settings.gcp_project_id, location=settings.gcp_location)


def get_embedding_model() -> TextEmbeddingModel:
    """Return a configured TextEmbeddingModel."""
    return TextEmbeddingModel.from_pretrained(settings.embedding_model)


# ---------------------------------------------------------------------------
# Quiz question pass-through
# ---------------------------------------------------------------------------


def extract_quiz_questions(quiz: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the raw question list from the quiz object."""
    return quiz.get("questions", [])


# ---------------------------------------------------------------------------
# Single embedding unit
# ---------------------------------------------------------------------------


async def embed_one(
    file_path: Path,
    model: TextEmbeddingModel,
    semaphore: asyncio.Semaphore,
    output_dir: Path,
    dry_run: bool,
    resume: bool,
) -> tuple[str, str, str]:
    """
    Embed the lesson content for a single approved JSON file.

    Returns (lesson_id, tier_slug, status) where status is one of:
    "embedded", "skipped", "failed".
    """
    # file_path is approved/{tier_slug}/{lesson_id}.json
    tier_slug = file_path.parent.name
    lesson_id_stem = file_path.stem  # e.g. "L04"
    label = f"[{lesson_id_stem} {tier_slug}]"
    output_path = output_dir / tier_slug / file_path.name

    if resume and output_path.exists():
        logger.info(f"{label} Skipped (already exists)")
        return lesson_id_stem, tier_slug, "skipped"

    if dry_run:
        logger.info(f"{label} Would embed -> {tier_slug}/{file_path.name}")
        return lesson_id_stem, tier_slug, "skipped"

    # Load approved JSON
    try:
        with file_path.open("r", encoding="utf-8") as f:
            data: dict[str, Any] = json.load(f)
    except Exception as exc:
        logger.error(f"{label} FAILED: Could not read input — {exc}")
        return lesson_id_stem, tier_slug, "failed"

    lesson_obj = data.get("lesson", {})
    quiz_obj = data.get("quiz", {})
    lesson_id: str = data.get("lesson_id", lesson_id_stem.upper())

    chunk_text = extract_chunk_text(lesson_obj)
    if not chunk_text.strip():
        logger.error(f"{label} FAILED: No extractable text from lesson")
        return lesson_id_stem, tier_slug, "failed"

    logger.info(f"{label} Embedding...")
    start = time.monotonic()

    async with semaphore:
        try:
            loop = asyncio.get_event_loop()
            embedding_input = TextEmbeddingInput(
                text=chunk_text, task_type="RETRIEVAL_DOCUMENT"
            )
            result = await loop.run_in_executor(
                None,
                lambda: model.get_embeddings([embedding_input]),
            )
            vector: list[float] = result[0].values
        except Exception as exc:
            elapsed = time.monotonic() - start
            logger.error(f"{label} FAILED ({elapsed:.1f}s): {exc}")
            return lesson_id_stem, tier_slug, "failed"

    elapsed = time.monotonic() - start

    if len(vector) != 768:
        logger.error(
            f"{label} FAILED ({elapsed:.1f}s): Expected 768-dim vector, got {len(vector)}"
        )
        return lesson_id_stem, tier_slug, "failed"

    output: dict[str, Any] = {
        "lesson_id": lesson_id,
        "tier": tier_slug,
        "chunk": {
            "text": chunk_text,
            "embedding": vector,
            "token_count": estimate_token_count(chunk_text),
        },
        "quiz_questions": extract_quiz_questions(quiz_obj),
        "lesson_metadata": {
            "title": lesson_obj.get("title", ""),
            "key_takeaways": lesson_obj.get("key_takeaways", []),
            "terminal_steps": lesson_obj.get("terminal_steps", []),
        },
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output_path.open("w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
    except OSError as exc:
        elapsed = time.monotonic() - start
        logger.error(f"{label} FAILED ({elapsed:.1f}s): Could not write output — {exc}")
        return lesson_id_stem, tier_slug, "failed"

    logger.info(f"{label} Done ({elapsed:.1f}s) — vector dim={len(vector)}")
    return lesson_id_stem, tier_slug, "embedded"


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


async def run_pipeline(
    files: list[Path],
    dry_run: bool,
    resume: bool,
) -> None:
    """Run the embedding pipeline over the given approved files."""
    if not dry_run:
        configure_vertexai()
        model = get_embedding_model()
    else:
        model = None  # type: ignore[assignment]

    EMBEDDED_DIR.mkdir(parents=True, exist_ok=True)

    semaphore = asyncio.Semaphore(settings.embedding_concurrency_limit)

    tasks = [
        embed_one(
            file_path=f,
            model=model,
            semaphore=semaphore,
            output_dir=EMBEDDED_DIR,
            dry_run=dry_run,
            resume=resume,
        )
        for f in files
    ]

    results = await asyncio.gather(*tasks, return_exceptions=False)

    counts: dict[str, int] = {"embedded": 0, "skipped": 0, "failed": 0}
    for _stem, _tier, status in results:
        counts[status] = counts.get(status, 0) + 1

    total = len(results)
    logger.info(
        f"\nSummary: {total} file(s) — "
        f"{counts['embedded']} embedded, "
        f"{counts['skipped']} skipped, "
        f"{counts['failed']} failed."
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Embed approved Linux Basics lesson content via Vertex AI."
    )
    parser.add_argument(
        "--lesson",
        metavar="L04",
        help="Embed only the specified lesson ID (e.g. L04). All tiers unless --tier given.",
    )
    parser.add_argument(
        "--tier",
        choices=TIERS,
        metavar="Beginner|Intermediate|Advanced",
        help="Embed only the specified tier. All lessons unless --lesson given.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be embedded without calling the API.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip files where the embedded output already exists.",
    )
    return parser.parse_args()


def collect_approved_files(lesson_filter: str | None, tier_filter: str | None) -> list[Path]:
    """
    Return the list of approved JSON files to embed, applying lesson/tier filters.
    Files are expected at: approved/{tier_slug}/{lesson_id}.json
    """
    if not APPROVED_DIR.exists():
        logger.error(
            f"Approved directory not found: {APPROVED_DIR}\n"
            "Run generate_content.py first to populate pipeline/approved/."
        )
        sys.exit(1)

    all_files = sorted(APPROVED_DIR.glob("*/*.json"))
    if not all_files:
        logger.error(f"No JSON files found in {APPROVED_DIR}")
        sys.exit(1)

    filtered: list[Path] = []
    for f in all_files:
        # Structure: approved/{tier_slug}/{lesson_id}.json
        file_tier_slug = f.parent.name.lower()
        file_lesson_id = f.stem.upper()

        if lesson_filter and file_lesson_id != lesson_filter.upper():
            continue

        if tier_filter:
            expected_slug = TIER_FILENAME.get(tier_filter, tier_filter.lower())
            if file_tier_slug != expected_slug:
                continue

        filtered.append(f)

    if not filtered:
        logger.error("No files matched the specified filters.")
        sys.exit(1)

    return filtered


def main() -> None:
    args = parse_args()
    files = collect_approved_files(args.lesson, args.tier)

    mode = "dry-run" if args.dry_run else "live"
    logger.info(
        f"Embedding pipeline starting — {len(files)} file(s), "
        f"model={settings.embedding_model}, "
        f"concurrency={settings.embedding_concurrency_limit}, mode={mode}"
    )
    if args.resume:
        logger.info("Resume mode: existing embedded files will be skipped.")

    asyncio.run(run_pipeline(files=files, dry_run=args.dry_run, resume=args.resume))


if __name__ == "__main__":
    main()
