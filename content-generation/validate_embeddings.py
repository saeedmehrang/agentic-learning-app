#!/usr/bin/env python3
"""
validate_embeddings.py — Spot-check embedded JSON files from the pipeline.

Reads up to --sample files per tier from pipeline/embedded/ (GCS or local,
determined by GCS_PIPELINE_BUCKET env var) and verifies:

  - Required top-level keys are present
  - lesson_id and tier values are non-empty strings
  - content_hash is a 64-character hex SHA-256
  - chunk.text is a non-empty string
  - chunk.embedding is a list of exactly 768 floats in [-1.0, 1.0]
  - chunk.token_count is a positive integer
  - quiz_questions is a non-empty list; each question has at least a "type" key
  - lesson_metadata has title, key_takeaways (list), and terminal_steps (list)

Usage:
    # Against GCS (set GCS_PIPELINE_BUCKET first):
    GCS_PIPELINE_BUCKET=agentic-learning-pipeline python content-generation/validate_embeddings.py

    # Against local pipeline outputs:
    python content-generation/validate_embeddings.py

    # Validate a specific lesson/tier:
    python content-generation/validate_embeddings.py --lesson L01 --tier beginner

    # Validate all files (not just a sample):
    python content-generation/validate_embeddings.py --sample 0
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from storage import get_storage_backend

TIERS = ["beginner", "intermediate", "advanced"]
EMBEDDING_DIM = 768


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


def _check(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def validate_file(path: str, data: dict[str, Any]) -> list[str]:
    """Return a list of validation errors for a single embedded JSON file."""
    errors: list[str] = []

    # Top-level keys
    required_keys = {"lesson_id", "tier", "content_hash", "chunk", "quiz_questions", "lesson_metadata"}
    missing = required_keys - data.keys()
    if missing:
        errors.append(f"Missing top-level keys: {sorted(missing)}")
        return errors  # can't validate further without the structure

    # lesson_id
    _check(isinstance(data["lesson_id"], str) and data["lesson_id"].strip(), "lesson_id is empty or not a string", errors)

    # tier
    _check(isinstance(data["tier"], str) and data["tier"] in TIERS, f"tier '{data['tier']}' not in {TIERS}", errors)

    # content_hash
    h = data["content_hash"]
    _check(isinstance(h, str) and len(h) == 64 and all(c in "0123456789abcdef" for c in h), f"content_hash is not a 64-char hex string: {h!r}", errors)

    # chunk
    chunk = data["chunk"]
    _check(isinstance(chunk, dict), "chunk is not a dict", errors)
    if isinstance(chunk, dict):
        # text
        _check(isinstance(chunk.get("text"), str) and chunk["text"].strip(), "chunk.text is empty or missing", errors)

        # embedding
        emb = chunk.get("embedding")
        _check(isinstance(emb, list), "chunk.embedding is not a list", errors)
        if isinstance(emb, list):
            _check(len(emb) == EMBEDDING_DIM, f"chunk.embedding has {len(emb)} dims, expected {EMBEDDING_DIM}", errors)
            non_float = [i for i, v in enumerate(emb) if not isinstance(v, (int, float))]
            _check(not non_float, f"chunk.embedding has non-numeric values at indices {non_float[:5]}", errors)
            if not non_float:
                out_of_range = [i for i, v in enumerate(emb) if not (-1.0 <= v <= 1.0)]
                if out_of_range:
                    errors.append(f"chunk.embedding has {len(out_of_range)} values outside [-1, 1] (first: index {out_of_range[0]})")

        # token_count
        tc = chunk.get("token_count")
        _check(isinstance(tc, int) and tc > 0, f"chunk.token_count is not a positive integer: {tc!r}", errors)

    # quiz_questions
    qqs = data["quiz_questions"]
    _check(isinstance(qqs, list) and len(qqs) > 0, f"quiz_questions is empty or not a list (got {type(qqs).__name__})", errors)
    if isinstance(qqs, list):
        for i, q in enumerate(qqs):
            _check(isinstance(q, dict) and "format" in q, f"quiz_questions[{i}] missing 'format' key", errors)

    # lesson_metadata
    meta = data["lesson_metadata"]
    _check(isinstance(meta, dict), "lesson_metadata is not a dict", errors)
    if isinstance(meta, dict):
        _check(isinstance(meta.get("title"), str) and meta["title"].strip(), "lesson_metadata.title is empty or missing", errors)
        _check(isinstance(meta.get("key_takeaways"), list), "lesson_metadata.key_takeaways is not a list", errors)
        _check(isinstance(meta.get("terminal_steps"), list), "lesson_metadata.terminal_steps is not a list", errors)

    return errors


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate embedded pipeline JSON files.")
    parser.add_argument("--lesson", metavar="L01", help="Validate only this lesson ID.")
    parser.add_argument("--tier", choices=TIERS, metavar="|".join(TIERS), help="Validate only this tier.")
    parser.add_argument("--sample", type=int, default=2, metavar="N", help="Max files per tier to validate (0 = all). Default: 2.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    storage = get_storage_backend()

    all_paths = storage.list_prefix("pipeline/embedded/")
    if not all_paths:
        print(f"ERROR: No embedded files found under pipeline/embedded/ in {storage.location}")
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

    # Sample: take up to N per tier
    if args.sample > 0:
        by_tier: dict[str, list[str]] = {}
        for path in filtered:
            tier_slug = path.split("/")[-2]
            by_tier.setdefault(tier_slug, []).append(path)
        sampled: list[str] = []
        for tier_paths in by_tier.values():
            sampled.extend(tier_paths[: args.sample])
        filtered = sorted(sampled)

    print(f"Validating {len(filtered)} file(s) from {storage.location}\n")

    total_errors = 0
    for path in filtered:
        try:
            data = storage.read_json(path)
        except Exception as exc:
            print(f"  FAIL  {path}\n        Could not read file: {exc}")
            total_errors += 1
            continue

        errors = validate_file(path, data)
        if errors:
            print(f"  FAIL  {path}")
            for e in errors:
                print(f"        - {e}")
            total_errors += len(errors)
        else:
            emb_dim = len(data["chunk"]["embedding"])
            n_questions = len(data["quiz_questions"])
            token_count = data["chunk"]["token_count"]
            print(f"  OK    {path}  (dim={emb_dim}, tokens≈{token_count}, questions={n_questions})")

    print(f"\n{'All files valid.' if total_errors == 0 else f'{total_errors} error(s) found.'}")
    sys.exit(0 if total_errors == 0 else 1)


if __name__ == "__main__":
    main()
