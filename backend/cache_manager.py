"""
Gemini context cache manager.

Reads approved lesson JSON files (from GCS or local filesystem), groups them
into 3 blocks of ~10 lessons each, and creates a Gemini CachedContent per
block at startup. Each subsequent LessonSession call reuses the block cache
handle so the prefix tokens are only charged once per cache TTL window.

Controlled by ENABLE_LESSON_CACHE env var (default: false).
When disabled, build_caches() is a no-op and get_cache() always returns None.

Block layout (10 lessons per block):
  block_0: L01–L10
  block_1: L11–L20
  block_2: L21–L29
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Block definition
# ---------------------------------------------------------------------------

_BLOCKS: list[list[str]] = [
    [f"L{i:02d}" for i in range(1, 11)],   # block_0: L01–L10
    [f"L{i:02d}" for i in range(11, 21)],  # block_1: L11–L20
    [f"L{i:02d}" for i in range(21, 30)],  # block_2: L21–L29
]

# lesson_id → block index
_LESSON_BLOCK: dict[str, int] = {
    lid: block_idx
    for block_idx, lesson_ids in enumerate(_BLOCKS)
    for lid in lesson_ids
}

# Cache TTL in seconds (1 hour — Gemini minimum is 60 s, maximum is 1 hour for Vertex AI)
_CACHE_TTL_SECONDS = 3600

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

# Each entry: {"handle": CachedContent, "expires_at": datetime}
_cache_store: dict[int, dict[str, Any]] = {}

_enabled: bool = False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_approved_files(approved_dir: Path) -> dict[str, dict[str, Any]]:
    """
    Load all approved lesson JSON files from local filesystem.

    Directory structure expected:
      approved_dir/
        beginner/L01.json
        intermediate/L01.json
        advanced/L01.json
        ...

    Returns dict keyed by "<lesson_id>:<tier>", value = parsed JSON dict.
    """
    content: dict[str, dict[str, Any]] = {}
    for tier_dir in approved_dir.iterdir():
        if not tier_dir.is_dir():
            continue
        tier = tier_dir.name
        for json_file in sorted(tier_dir.glob("L*.json")):
            lesson_id = json_file.stem  # e.g. "L01"
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
                content[f"{lesson_id}:{tier}"] = data
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to load %s: %s", json_file, exc)
    return content


def _build_block_prompt(block_lessons: list[str], content: dict[str, dict[str, Any]]) -> str:
    """
    Concatenate lesson JSON files for a block into a structured prompt string.
    XML-tagged by lesson_id and tier for clear model boundaries.
    """
    parts: list[str] = ["<course_content>"]
    for lesson_id in block_lessons:
        for tier in ("beginner", "intermediate", "advanced"):
            key = f"{lesson_id}:{tier}"
            if key in content:
                parts.append(f'<lesson id="{lesson_id}" tier="{tier}">')
                parts.append(json.dumps(content[key], ensure_ascii=False))
                parts.append("</lesson>")
    parts.append("</course_content>")
    return "\n".join(parts)


def _create_cache(block_idx: int, prompt: str) -> Any:
    """
    Create a Gemini CachedContent for a block.
    Returns the cache handle (google.generativeai.caching.CachedContent).
    """
    from google.generativeai import caching  # type: ignore[import-untyped]

    cache = caching.CachedContent.create(
        model="models/gemini-2.5-flash",
        contents=[prompt],
        ttl_seconds=_CACHE_TTL_SECONDS,
        display_name=f"linux-basics-block-{block_idx}",
    )
    logger.info("block_%d cache created", block_idx, extra={"cache_name": cache.name})
    return cache


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_caches(approved_dir: Path | None = None) -> None:
    """
    Build Gemini context caches for all 3 lesson blocks at startup.

    No-op when ENABLE_LESSON_CACHE != "true".

    Args:
        approved_dir: path to approved/ directory. Defaults to
                      <repo_root>/courses/linux-basics/pipeline/approved/
                      Accepts override for testing.
    """
    global _enabled

    if os.environ.get("ENABLE_LESSON_CACHE", "false").lower() != "true":
        logger.info("Lesson cache disabled (ENABLE_LESSON_CACHE != true)")
        _enabled = False
        return

    _enabled = True

    if approved_dir is None:
        approved_dir = (
            Path(__file__).resolve().parent.parent
            / "courses" / "linux-basics" / "pipeline" / "approved"
        )

    if not approved_dir.exists():
        logger.warning(
            "Approved content directory not found — lesson cache disabled: %s", approved_dir
        )
        _enabled = False
        return

    content = _load_approved_files(approved_dir)
    if not content:
        logger.warning("No approved lesson files found — lesson cache disabled")
        _enabled = False
        return

    for block_idx, block_lessons in enumerate(_BLOCKS):
        prompt = _build_block_prompt(block_lessons, content)
        try:
            handle = _create_cache(block_idx, prompt)
            _cache_store[block_idx] = {
                "handle": handle,
                "expires_at": datetime.now(tz=timezone.utc).replace(
                    second=datetime.now(tz=timezone.utc).second
                ).replace(microsecond=0),
            }
            # Store prompt for lazy refresh
            _cache_store[block_idx]["prompt"] = prompt
            _cache_store[block_idx]["expires_at"] = datetime.now(
                tz=timezone.utc
            ).timestamp() + _CACHE_TTL_SECONDS
        except Exception as exc:
            logger.error("Failed to create cache for block_%d: %s", block_idx, exc, exc_info=True)


def get_cache(lesson_id: str) -> Any | None:
    """
    Return the CachedContent handle for the block containing lesson_id,
    or None if caching is disabled, the lesson is unknown, or the handle
    has expired (lazy refresh attempted on expiry).

    Args:
        lesson_id: e.g. "L01"

    Returns:
        CachedContent handle or None
    """
    if not _enabled:
        return None

    block_idx = _LESSON_BLOCK.get(lesson_id)
    if block_idx is None:
        logger.warning("Unknown lesson_id for cache lookup: %s", lesson_id)
        return None

    entry = _cache_store.get(block_idx)
    if entry is None:
        return None

    # Check TTL — lazy refresh on expiry
    now_ts = datetime.now(tz=timezone.utc).timestamp()
    if now_ts >= entry["expires_at"]:
        logger.info("Cache TTL expired for block_%d — refreshing", block_idx)
        try:
            handle = _create_cache(block_idx, entry["prompt"])
            entry["handle"] = handle
            entry["expires_at"] = now_ts + _CACHE_TTL_SECONDS
        except Exception as exc:
            logger.error("Cache refresh failed for block_%d: %s", block_idx, exc, exc_info=True)
            return None

    return entry["handle"]


def is_enabled() -> bool:
    """Return True if lesson caching is enabled and caches were built."""
    return _enabled
