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
import hashlib
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

import vertexai
from vertexai.language_models import TextEmbeddingInput, TextEmbeddingModel

from config import settings
from storage import StorageBackend, get_storage_backend

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

# Pipeline file I/O is routed through a storage backend.
# Set GCS_PIPELINE_BUCKET env var to use GCS; leave unset for local filesystem.
storage: StorageBackend = get_storage_backend()

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
    approved_rel: str,
    model: TextEmbeddingModel,
    semaphore: asyncio.Semaphore,
    dry_run: bool,
    resume: bool,
) -> tuple[str, str, str]:
    """
    Embed the lesson content for a single approved JSON file.

    approved_rel is a relative path like "pipeline/approved/beginner/L04.json".
    Returns (lesson_id, tier_slug, status) where status is one of:
    "embedded", "skipped", "failed".
    """
    # Parse tier_slug and lesson_id from the relative path
    # Structure: pipeline/approved/{tier_slug}/{lesson_id}.json
    parts = approved_rel.split("/")
    tier_slug = parts[-2]
    lesson_id_stem = parts[-1].replace(".json", "")
    label = f"[{lesson_id_stem} {tier_slug}]"
    embedded_rel = f"pipeline/embedded/{tier_slug}/{lesson_id_stem}.json"

    if resume and storage.exists(embedded_rel):
        logger.info(f"{label} Skipped (already exists)")
        return lesson_id_stem, tier_slug, "skipped"

    if dry_run:
        logger.info(f"{label} Would embed -> {tier_slug}/{lesson_id_stem}.json")
        return lesson_id_stem, tier_slug, "skipped"

    # Load approved JSON
    try:
        data: dict[str, Any] = storage.read_json(approved_rel)
    except Exception as exc:
        logger.error(f"{label} FAILED: Could not read input — {exc}")
        return lesson_id_stem, tier_slug, "failed"

    content_hash = hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()

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
        "content_hash": content_hash,
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

    try:
        storage.write_json(embedded_rel, output)
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
    files: list[str],
    dry_run: bool,
    resume: bool,
) -> None:
    """Run the embedding pipeline over the given approved relative paths."""
    if not dry_run:
        configure_vertexai()
        model = get_embedding_model()
    else:
        model = None  # type: ignore[assignment]

    semaphore = asyncio.Semaphore(settings.embedding_concurrency_limit)

    tasks = [
        embed_one(
            approved_rel=f,
            model=model,
            semaphore=semaphore,
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


def collect_approved_files(lesson_filter: str | None, tier_filter: str | None) -> list[str]:
    """
    Return the list of approved relative paths to embed, applying lesson/tier filters.
    Paths have the form: "pipeline/approved/{tier_slug}/{lesson_id}.json"
    """
    all_files = storage.list_prefix("pipeline/approved/")
    if not all_files:
        logger.error(
            "No approved JSON files found under pipeline/approved/.\n"
            "Run generate_content.py first to populate pipeline/approved/."
        )
        sys.exit(1)

    filtered: list[str] = []
    for rel_path in all_files:
        # Structure: pipeline/approved/{tier_slug}/{lesson_id}.json
        parts = rel_path.split("/")
        file_tier_slug = parts[-2].lower()
        file_lesson_id = parts[-1].replace(".json", "").upper()

        if lesson_filter and file_lesson_id != lesson_filter.upper():
            continue

        if tier_filter:
            expected_slug = TIER_FILENAME.get(tier_filter, tier_filter.lower())
            if file_tier_slug != expected_slug:
                continue

        filtered.append(rel_path)

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
        f"concurrency={settings.embedding_concurrency_limit}, mode={mode}, "
        f"storage={storage.location}"
    )
    if args.resume:
        logger.info("Resume mode: existing embedded files will be skipped.")

    asyncio.run(run_pipeline(files=files, dry_run=args.dry_run, resume=args.resume))


if __name__ == "__main__":
    main()
