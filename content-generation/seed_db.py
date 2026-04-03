#!/usr/bin/env python3
"""
seed_db.py — Cloud SQL seeding job for Linux Basics course content.

Reads all embedded JSON files from courses/linux-basics/pipeline/embedded/,
then bulk-inserts lessons, content chunks (with pgvector embeddings), and quiz
questions into the Cloud SQL learning_app database.

Designed to run as a Cloud Run Job using the Cloud SQL Python Connector,
which establishes a TLS tunnel via the Cloud SQL Admin API — no VPC connector
or public IP required.

All inserts are idempotent: re-running this script on a database that already
has data will not create duplicate rows.

Environment variables (injected by Cloud Run Job secret bindings):
  DB_PASSWORD                  — database password (from Secret Manager)
  DB_INSTANCE_CONNECTION_NAME  — e.g. agentic-learning-app-e13cb:us-central1:learning-app-db

Usage (local testing via Cloud SQL Auth Proxy):
    DB_PASSWORD=... DB_INSTANCE_CONNECTION_NAME=... python seed_db.py [--dry-run]

Usage (Cloud Run Job):
    gcloud run jobs execute content-seed --region us-central1 --wait
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from pathlib import Path
from typing import Any

import pg8000.native
import yaml
from google.cloud.sql.connector import Connector
from pydantic_settings import BaseSettings, SettingsConfigDict

from storage import StorageBackend, get_storage_backend

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

OUTLINES_PATH = REPO_ROOT / "courses" / "linux-basics" / "outlines.yaml"

# Pipeline file I/O is routed through a storage backend.
# Set GCS_PIPELINE_BUCKET env var to use GCS; leave unset for local filesystem.
storage: StorageBackend = get_storage_backend()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class SeedSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    db_instance_connection_name: str = ""  # required in production
    db_user: str = "app_user"
    db_password: str = ""  # required; injected via Secret Manager binding
    db_name: str = "learning_app"

    # Allow override for local testing (e.g. pointing at a local proxy)
    db_host: str = ""
    db_port: int = 5432


seed_settings = SeedSettings()

# ---------------------------------------------------------------------------
# Format mapping
# ---------------------------------------------------------------------------

FORMAT_MAP: dict[str, str] = {
    "multiple_choice": "mc",
    "true_false": "tf",
    "fill_blank": "fill",
    "command_completion": "command",
}

# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------


def load_outlines(path: Path) -> dict[str, dict[str, Any]]:
    """
    Load outlines.yaml and return a lookup dict keyed by lesson_id.
    Each value contains: module_id, title, prerequisites.
    """
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, list):
        raise ValueError(f"Expected a YAML list in {path}")
    return {
        lesson["lesson_id"]: {
            "module_id": lesson["module_id"],
            "title": lesson["title"],
            "prerequisites": lesson.get("prerequisites", []),
        }
        for lesson in data
    }


def load_embedded_files() -> list[str]:
    """Return sorted list of embedded relative paths from storage."""
    files = storage.list_prefix("pipeline/embedded/")
    if not files:
        logger.error(
            "No embedded JSON files found under pipeline/embedded/.\n"
            "Run embed_content.py first to populate pipeline/embedded/."
        )
        sys.exit(1)
    return files


# ---------------------------------------------------------------------------
# Distractor extraction
# ---------------------------------------------------------------------------


def extract_distractors(
    format_code: str,
    options: list[str],
    answer: str,
) -> list[str]:
    """
    Derive distractor strings from options[] based on question format.

    - mc: options are labelled ('A. ...', 'B. ...'). Distractors are all
          options whose label letter does not match the answer letter.
    - fill / command: options are unlabelled exact strings. Distractors are
          all options that are not the answer.
    - tf: no distractors.
    """
    if format_code == "tf" or not options:
        return []

    if format_code == "mc":
        # answer is a letter like 'B'; distractor = options not starting with that letter
        answer_prefix = answer.strip().upper() + "."
        return [opt for opt in options if not opt.strip().startswith(answer_prefix)]

    # fill / command: unlabelled options
    return [opt for opt in options if opt != answer]


# ---------------------------------------------------------------------------
# Database connection
# ---------------------------------------------------------------------------


def get_connection(connector: Connector) -> pg8000.native.Connection:
    """Open a pg8000 connection via the Cloud SQL Python Connector."""
    return connector.connect(
        instance_connection_name=seed_settings.db_instance_connection_name,
        driver="pg8000",
        user=seed_settings.db_user,
        password=seed_settings.db_password,
        db=seed_settings.db_name,
    )


# ---------------------------------------------------------------------------
# Seeding functions
# ---------------------------------------------------------------------------


def seed_lessons(
    conn: pg8000.native.Connection,
    lesson_id: str,
    outline: dict[str, Any],
) -> None:
    """Upsert a lesson row. Updates title/prerequisites when content changes."""
    content_hash = hashlib.sha256(
        json.dumps(
            {
                "module_id": outline["module_id"],
                "title": outline["title"],
                "prerequisites": outline["prerequisites"],
            },
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    conn.run(
        """
        INSERT INTO lessons (lesson_id, module_id, title, prerequisites, concept_tags, content_hash)
        VALUES (:lesson_id, :module_id, :title, :prerequisites, :concept_tags, :content_hash)
        ON CONFLICT (lesson_id) DO UPDATE
            SET module_id     = EXCLUDED.module_id,
                title         = EXCLUDED.title,
                prerequisites = EXCLUDED.prerequisites,
                content_hash  = EXCLUDED.content_hash
            WHERE lessons.content_hash IS DISTINCT FROM EXCLUDED.content_hash
        """,
        lesson_id=lesson_id,
        module_id=outline["module_id"],
        title=outline["title"],
        prerequisites=outline["prerequisites"],
        concept_tags=[],  # filled in Phase 1.1
        content_hash=content_hash,
    )


def seed_content_chunk(
    conn: pg8000.native.Connection,
    lesson_id: str,
    tier_slug: str,
    chunk: dict[str, Any],
    content_hash: str | None,
) -> None:
    """
    Upsert a content_chunk row.
    Only writes when content_hash differs from the stored value, avoiding
    unnecessary 768-dim vector writes to pgvector on unchanged content.
    """
    embedding_str = "[" + ",".join(str(v) for v in chunk["embedding"]) + "]"
    conn.run(
        """
        INSERT INTO content_chunks (lesson_id, tier, content_text, embedding, token_count, content_hash)
        VALUES (:lesson_id, :tier, :content_text, :embedding::vector, :token_count, :content_hash)
        ON CONFLICT (lesson_id, tier) DO UPDATE
            SET content_text = EXCLUDED.content_text,
                embedding    = EXCLUDED.embedding,
                token_count  = EXCLUDED.token_count,
                content_hash = EXCLUDED.content_hash
            WHERE content_chunks.content_hash IS DISTINCT FROM EXCLUDED.content_hash
        """,
        lesson_id=lesson_id,
        tier=tier_slug,
        content_text=chunk["text"],
        embedding=embedding_str,
        token_count=chunk.get("token_count", 0),
        content_hash=content_hash,
    )


def seed_quiz_question(
    conn: pg8000.native.Connection,
    lesson_id: str,
    tier_slug: str,
    question: dict[str, Any],
) -> None:
    """Upsert a quiz question row. Updates content fields when the hash changes."""
    raw_format = question.get("format", "")
    format_code = FORMAT_MAP.get(raw_format, raw_format)

    options: list[str] = question.get("options", [])
    answer: str = question.get("answer", "")
    distractors = extract_distractors(format_code, options, answer)
    explanation: str = question.get("explanation", "")
    options_json = json.dumps(options) if options else None
    learning_objective_ref = question.get("learning_objective_ref")

    content_hash = hashlib.sha256(
        json.dumps(
            {
                "question_text": question.get("question", ""),
                "correct_answer": answer,
                "distractors": distractors,
                "explanation": explanation,
                "options_json": options_json,
            },
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()

    conn.run(
        """
        INSERT INTO quiz_questions (
            question_id, lesson_id, tier, format,
            question_text, correct_answer, distractors, explanation,
            options_json, learning_objective_ref, content_hash
        )
        VALUES (
            :question_id, :lesson_id, :tier, :format,
            :question_text, :correct_answer, :distractors, :explanation,
            :options_json, :learning_objective_ref, :content_hash
        )
        ON CONFLICT (question_id) DO UPDATE
            SET question_text          = EXCLUDED.question_text,
                correct_answer         = EXCLUDED.correct_answer,
                distractors            = EXCLUDED.distractors,
                explanation            = EXCLUDED.explanation,
                options_json           = EXCLUDED.options_json,
                learning_objective_ref = EXCLUDED.learning_objective_ref,
                content_hash           = EXCLUDED.content_hash
            WHERE quiz_questions.content_hash IS DISTINCT FROM EXCLUDED.content_hash
        """,
        question_id=question.get("question_id", ""),
        lesson_id=lesson_id,
        tier=tier_slug,
        format=format_code,
        question_text=question.get("question", ""),
        correct_answer=answer,
        distractors=distractors,
        explanation=explanation,
        options_json=options_json,
        learning_objective_ref=learning_objective_ref,
        content_hash=content_hash,
    )


# ---------------------------------------------------------------------------
# Main seeding loop
# ---------------------------------------------------------------------------


def seed_file(
    conn: pg8000.native.Connection,
    rel_path: str,
    outlines_lookup: dict[str, dict[str, Any]],
    dry_run: bool,
) -> tuple[str, str]:
    """
    Process one embedded JSON file. Returns (lesson_id, status).
    status is "seeded" | "skipped" | "failed".
    rel_path is a relative path like "pipeline/embedded/beginner/L04.json".
    """
    stem = rel_path.split("/")[-1].replace(".json", "")
    try:
        data: dict[str, Any] = storage.read_json(rel_path)
    except Exception as exc:
        logger.error(f"[{stem}] FAILED: Could not read file — {exc}")
        return stem, "failed"

    lesson_id: str = data.get("lesson_id", "")
    tier_slug: str = data.get("tier", "")
    content_hash: str | None = data.get("content_hash")
    chunk: dict[str, Any] = data.get("chunk", {})
    quiz_questions: list[dict[str, Any]] = data.get("quiz_questions", [])
    label = f"[{lesson_id} {tier_slug}]"

    if not lesson_id or not tier_slug:
        logger.error(f"[{file_path.stem}] FAILED: Missing lesson_id or tier")
        return file_path.stem, "failed"

    outline = outlines_lookup.get(lesson_id)
    if outline is None:
        logger.error(f"{label} FAILED: lesson_id '{lesson_id}' not found in outlines.yaml")
        return lesson_id, "failed"

    if not chunk.get("embedding"):
        logger.error(f"{label} FAILED: No embedding vector in chunk data")
        return lesson_id, "failed"

    if dry_run:
        q_count = len(quiz_questions)
        logger.info(f"{label} Would seed — chunk + {q_count} question(s)")
        return lesson_id, "skipped"

    try:
        seed_lessons(conn, lesson_id, outline)
        seed_content_chunk(conn, lesson_id, tier_slug, chunk, content_hash)
        for question in quiz_questions:
            seed_quiz_question(conn, lesson_id, tier_slug, question)
        logger.info(
            f"{label} Seeded — 1 chunk + {len(quiz_questions)} question(s)"
        )
        return lesson_id, "seeded"
    except Exception as exc:
        logger.error(f"{label} FAILED: DB error — {exc}")
        return lesson_id, "failed"


def run_seed(dry_run: bool) -> None:
    """Load all embedded files and seed Cloud SQL."""
    try:
        outlines_lookup = load_outlines(OUTLINES_PATH)
    except Exception as exc:
        logger.error(f"Failed to load outlines: {exc}")
        sys.exit(1)

    files = load_embedded_files()
    logger.info(f"Seeding {len(files)} embedded file(s) into Cloud SQL...")

    if dry_run:
        logger.info("Dry-run mode: no database writes.")
        for rel_path in files:
            stem = rel_path.split("/")[-1].replace(".json", "")
            try:
                data = storage.read_json(rel_path)
                lesson_id = data.get("lesson_id", stem)
                tier_slug = data.get("tier", "")
                q_count = len(data.get("quiz_questions", []))
                logger.info(f"[{lesson_id} {tier_slug}] Would seed — 1 chunk + {q_count} question(s)")
            except Exception as exc:
                logger.error(f"[{stem}] FAILED: {exc}")
        return

    if not seed_settings.db_instance_connection_name:
        logger.error(
            "DB_INSTANCE_CONNECTION_NAME is not set. "
            "Set it via environment variable or Secret Manager binding."
        )
        sys.exit(1)
    if not seed_settings.db_password:
        logger.error(
            "DB_PASSWORD is not set. "
            "Set it via environment variable or Secret Manager binding."
        )
        sys.exit(1)

    connector = Connector()
    try:
        conn = get_connection(connector)
        counts: dict[str, int] = {"seeded": 0, "skipped": 0, "failed": 0}
        for rel_path in files:
            _, status = seed_file(conn, rel_path, outlines_lookup, dry_run=False)
            counts[status] = counts.get(status, 0) + 1
        conn.close()
    finally:
        connector.close()

    total = len(files)
    logger.info(
        f"\nSummary: {total} file(s) — "
        f"{counts['seeded']} seeded, "
        f"{counts['skipped']} skipped, "
        f"{counts['failed']} failed."
    )
    if counts["failed"] > 0:
        sys.exit(1)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed Cloud SQL with embedded Linux Basics content."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be seeded without writing to the database.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_seed(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
