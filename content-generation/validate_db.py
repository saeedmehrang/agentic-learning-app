#!/usr/bin/env python3
"""
validate_db.py — Spot-check Cloud SQL content after seeding.

Connects via the Cloud SQL Python Connector (same as seed_db.py) and verifies:

  lessons table
    - Row exists for each sampled lesson_id
    - module_id, title are non-empty strings
    - content_hash is a 64-char hex string

  content_chunks table
    - Row exists for each (lesson_id, tier) combination
    - content_text is non-empty
    - embedding dimension is exactly 768
    - token_count is a positive integer
    - content_hash matches the value in the embedded JSON (GCS or local)

  quiz_questions table
    - Expected number of questions per (lesson_id, tier)
    - Each question has non-empty question_text and correct_answer
    - format is one of the known codes (mc, tf, fill, command)

Usage:
    # Sample 1 lesson per tier (default):
    GCS_PIPELINE_BUCKET=agentic-learning-pipeline \\
      DB_INSTANCE_CONNECTION_NAME=... DB_PASSWORD=... \\
      python content-generation/validate_db.py

    # Check a specific lesson/tier:
    ... python content-generation/validate_db.py --lesson L01 --tier beginner

    # Check all seeded content:
    ... python content-generation/validate_db.py --sample 0

Requires:
    DB_INSTANCE_CONNECTION_NAME and DB_PASSWORD env vars (or .env file).
    Application Default Credentials for Cloud SQL connector IAM auth.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import pg8000.dbapi
from google.cloud.sql.connector import Connector
from pydantic_settings import BaseSettings, SettingsConfigDict

from storage import get_storage_backend

REPO_ROOT = Path(__file__).resolve().parent.parent
TIERS = ["beginner", "intermediate", "advanced"]
KNOWN_FORMATS = {"mc", "tf", "fill", "command"}
EMBEDDING_DIM = 768


# ---------------------------------------------------------------------------
# Settings (reuse same env vars as seed_db.py)
# ---------------------------------------------------------------------------


class ValidateSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    db_instance_connection_name: str = ""
    db_user: str = "app_user"
    db_password: str = ""
    db_name: str = "learning_app"


settings = ValidateSettings()


# ---------------------------------------------------------------------------
# DB connection
# ---------------------------------------------------------------------------


def get_connection(connector: Connector) -> pg8000.dbapi.Connection:
    return connector.connect(
        instance_connection_string=settings.db_instance_connection_name,
        driver="pg8000",
        user=settings.db_user,
        password=settings.db_password,
        db=settings.db_name,
    )


def query(conn: pg8000.dbapi.Connection, sql: str, params: tuple = ()) -> list[tuple]:
    cur = conn.cursor()
    cur.execute(sql, params)
    return cur.fetchall()


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def _check(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def validate_lesson(conn: pg8000.dbapi.Connection, lesson_id: str) -> list[str]:
    errors: list[str] = []
    rows = query(conn, "SELECT module_id, title, content_hash FROM lessons WHERE lesson_id = %s", (lesson_id,))
    _check(len(rows) == 1, f"lessons: no row for lesson_id='{lesson_id}'", errors)
    if not rows:
        return errors
    module_id, title, content_hash = rows[0]
    _check(module_id is not None, f"lessons: module_id is NULL for '{lesson_id}'", errors)
    _check(isinstance(title, str) and title.strip(), f"lessons: title empty for '{lesson_id}'", errors)
    _check(isinstance(content_hash, str) and len(content_hash) == 64, f"lessons: content_hash invalid for '{lesson_id}'", errors)
    return errors


def validate_chunk(
    conn: pg8000.dbapi.Connection,
    lesson_id: str,
    tier: str,
    expected_hash: str | None,
) -> list[str]:
    errors: list[str] = []
    rows = query(
        conn,
        """
        SELECT content_text, token_count, content_hash,
               array_length(embedding::real[], 1) AS emb_dim
        FROM content_chunks
        WHERE lesson_id = %s AND tier = %s
        """,
        (lesson_id, tier),
    )
    label = f"content_chunks [{lesson_id} {tier}]"
    _check(len(rows) == 1, f"{label}: row missing", errors)
    if not rows:
        return errors
    content_text, token_count, content_hash, emb_dim = rows[0]
    _check(isinstance(content_text, str) and content_text.strip(), f"{label}: content_text empty", errors)
    _check(isinstance(token_count, int) and token_count > 0, f"{label}: token_count={token_count!r} not a positive int", errors)
    _check(emb_dim == EMBEDDING_DIM, f"{label}: embedding dim={emb_dim}, expected {EMBEDDING_DIM}", errors)
    if expected_hash:
        _check(content_hash == expected_hash, f"{label}: content_hash mismatch (DB={content_hash[:8]}… vs file={expected_hash[:8]}…)", errors)
    return errors


def validate_questions(
    conn: pg8000.dbapi.Connection,
    lesson_id: str,
    tier: str,
    expected_count: int,
) -> list[str]:
    errors: list[str] = []
    rows = query(
        conn,
        """
        SELECT question_id, question_text, correct_answer, format
        FROM quiz_questions
        WHERE lesson_id = %s AND tier = %s
        """,
        (lesson_id, tier),
    )
    label = f"quiz_questions [{lesson_id} {tier}]"
    _check(
        len(rows) == expected_count,
        f"{label}: {len(rows)} question(s) found, expected {expected_count}",
        errors,
    )
    for qid, qtext, answer, fmt in rows:
        _check(isinstance(qtext, str) and qtext.strip(), f"{label}: question_id={qid} has empty question_text", errors)
        _check(isinstance(answer, str) and answer.strip(), f"{label}: question_id={qid} has empty correct_answer", errors)
        _check(fmt in KNOWN_FORMATS, f"{label}: question_id={qid} has unknown format='{fmt}'", errors)
    return errors


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate Cloud SQL content after seeding.")
    parser.add_argument("--lesson", metavar="L01", help="Validate only this lesson ID.")
    parser.add_argument("--tier", choices=TIERS, metavar="|".join(TIERS), help="Validate only this tier.")
    parser.add_argument("--sample", type=int, default=1, metavar="N", help="Max files per tier to validate (0 = all). Default: 1.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not settings.db_instance_connection_name or not settings.db_password:
        print("ERROR: DB_INSTANCE_CONNECTION_NAME and DB_PASSWORD must be set.")
        sys.exit(1)

    storage = get_storage_backend()
    all_paths = storage.list_prefix("pipeline/embedded/")
    if not all_paths:
        print(f"ERROR: No embedded files found in {storage.location}")
        sys.exit(1)

    # Apply filters
    filtered: list[str] = []
    for path in all_paths:
        parts = path.split("/")
        tier_slug = parts[-2]
        lesson_stem = parts[-1].replace(".json", "").upper()
        if args.lesson and lesson_stem != args.lesson.upper():
            continue
        if args.tier and tier_slug != args.tier:
            continue
        filtered.append(path)

    if not filtered:
        print("ERROR: No files matched the specified filters.")
        sys.exit(1)

    # Sample per tier
    if args.sample > 0:
        by_tier: dict[str, list[str]] = {}
        for path in filtered:
            tier_slug = path.split("/")[-2]
            by_tier.setdefault(tier_slug, []).append(path)
        sampled: list[str] = []
        for tier_paths in by_tier.values():
            sampled.extend(tier_paths[: args.sample])
        filtered = sorted(sampled)

    print(f"Validating {len(filtered)} embedded file(s) against Cloud SQL...\n")

    connector = Connector()
    conn = get_connection(connector)

    total_errors = 0
    for path in filtered:
        parts = path.split("/")
        tier_slug = parts[-2]
        lesson_id = parts[-1].replace(".json", "").upper()

        try:
            file_data: dict[str, Any] = storage.read_json(path)
        except Exception as exc:
            print(f"  FAIL  [{lesson_id} {tier_slug}] Could not read embedded file: {exc}")
            total_errors += 1
            continue

        expected_hash: str | None = file_data.get("content_hash")
        expected_q_count = len(file_data.get("quiz_questions", []))

        errors: list[str] = []
        errors += validate_lesson(conn, lesson_id)
        errors += validate_chunk(conn, lesson_id, tier_slug, expected_hash)
        errors += validate_questions(conn, lesson_id, tier_slug, expected_q_count)

        if errors:
            print(f"  FAIL  [{lesson_id} {tier_slug}]")
            for e in errors:
                print(f"        - {e}")
            total_errors += len(errors)
        else:
            rows = query(conn, "SELECT token_count FROM content_chunks WHERE lesson_id = %s AND tier = %s", (lesson_id, tier_slug))
            token_count = rows[0][0] if rows else "?"
            print(f"  OK    [{lesson_id} {tier_slug}]  (dim={EMBEDDING_DIM}, tokens≈{token_count}, questions={expected_q_count})")

    connector.close()
    print(f"\n{'All checks passed.' if total_errors == 0 else f'{total_errors} error(s) found.'}")
    sys.exit(0 if total_errors == 0 else 1)


if __name__ == "__main__":
    main()
