"""
Gemini context cache manager and course content loader.

Responsibilities
----------------
1. **Content loading** (always runs at startup):
   Load all approved lesson JSON files, outlines.yaml, and concept_map.json from
   GCS (production) or the local filesystem (dev / tests). Returns an in-memory
   dict that main.py stores for the lifetime of the process.

2. **Gemini context caching** (optional, controlled by ENABLE_LESSON_CACHE):
   Group lessons into 3 blocks of ~10 lessons each, create a Gemini CachedContent
   per block, and refresh lazily on TTL expiry. When disabled, build_caches() still
   loads content and returns it — only the Gemini cache creation is skipped.

Content source selection
------------------------
- GCS_PIPELINE_BUCKET set  →  GcsBackend  (google-cloud-storage)
- GCS_PIPELINE_BUCKET unset →  LocalBackend  (pathlib, repo-relative)

GCS path layout (all under linux-basics/ prefix in the bucket):
  linux-basics/pipeline/approved/{tier}/L##.json   ← 87 lesson files
  linux-basics/outlines.yaml
  linux-basics/concept_map.json

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
# GCS key constants
# ---------------------------------------------------------------------------

# All course objects live under this prefix in the bucket.
_GCS_PREFIX = "linux-basics"
# Relative path (under _GCS_PREFIX) for the approved lessons directory.
_APPROVED_REL_PREFIX = "pipeline/approved/"

# ---------------------------------------------------------------------------
# Block definitions for Gemini context caching
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

# Cache TTL — 1 hour (Gemini minimum 60 s, maximum 1 h for Vertex AI)
_CACHE_TTL_SECONDS = 3600

# ---------------------------------------------------------------------------
# Module-level state (Gemini cache handles only — lesson content lives in main.py)
# ---------------------------------------------------------------------------

# Each entry: {"handle": CachedContent, "prompt": str, "expires_at": float}
_cache_store: dict[int, dict[str, Any]] = {}

_enabled: bool = False


# ---------------------------------------------------------------------------
# Local filesystem loader
# ---------------------------------------------------------------------------


def _load_from_local(approved_dir: Path) -> dict[str, dict[str, Any]]:
    """
    Load all approved lesson JSON files from the local filesystem.

    Expected directory layout:
        approved_dir/
            beginner/L01.json
            intermediate/L01.json
            advanced/L01.json
            ...

    Returns:
        Dict keyed by "{lesson_id}:{tier}" (e.g. "L01:beginner"), value = parsed JSON.
    """
    if not approved_dir.exists():
        logger.warning("Approved content directory not found: %s", approved_dir)
        return {}
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


# Backward-compat alias — existing tests call _load_approved_files(path) directly.
def _load_approved_files(approved_dir: Path) -> dict[str, dict[str, Any]]:
    return _load_from_local(approved_dir)


# ---------------------------------------------------------------------------
# GCS loaders
# ---------------------------------------------------------------------------


def _load_from_gcs(bucket_name: str) -> dict[str, dict[str, Any]]:
    """
    Load all approved lesson JSON files from GCS.

    Lists all blobs under ``linux-basics/pipeline/approved/`` in the bucket,
    downloads each, and parses the JSON. Blobs that cannot be parsed are
    logged as warnings and skipped — a partial load is better than a crash.

    Args:
        bucket_name: GCS bucket name, e.g. "agentic-learning-pipeline".

    Returns:
        Dict keyed by "{lesson_id}:{tier}", value = parsed lesson JSON dict.
    """
    from google.cloud import storage as gcs  # lazy — only needed in production

    client = gcs.Client()
    prefix = f"{_GCS_PREFIX}/{_APPROVED_REL_PREFIX}"
    blobs = list(client.list_blobs(bucket_name, prefix=prefix))
    logger.info(
        "Loading approved lessons from GCS — found %d blobs under gs://%s/%s",
        len(blobs), bucket_name, prefix,
    )
    content: dict[str, dict[str, Any]] = {}
    key_prefix = f"{_GCS_PREFIX}/"
    for blob in blobs:
        if not blob.name.endswith(".json"):
            continue
        # blob.name  = "linux-basics/pipeline/approved/beginner/L01.json"
        rel = blob.name[len(key_prefix):]              # "pipeline/approved/beginner/L01.json"
        parts = rel.split("/")  # ["pipeline", "approved", "beginner", "L01.json"]
        if len(parts) != 4:
            logger.warning("Unexpected GCS blob path (skipping): %s", blob.name)
            continue
        tier = parts[2]
        lesson_id = parts[3].removesuffix(".json")     # "L01"
        try:
            data = json.loads(blob.download_as_text(encoding="utf-8"))
            content[f"{lesson_id}:{tier}"] = data
        except Exception as exc:
            logger.warning("Failed to load GCS blob %s: %s", blob.name, exc)
    return content


def _load_yaml_from_gcs(bucket_name: str, gcs_key: str) -> Any:
    """
    Download and YAML-parse a single object from GCS.

    Args:
        bucket_name: GCS bucket name.
        gcs_key: Full GCS object key (e.g. "linux-basics/outlines.yaml").

    Returns:
        Parsed Python object (list or dict, depending on the YAML).

    Raises:
        FileNotFoundError: If the object does not exist.
        ValueError: If the content cannot be parsed as YAML.
    """
    import yaml  # already in dependencies (pyyaml)
    from google.cloud import storage as gcs

    client = gcs.Client()
    blob = client.bucket(bucket_name).blob(gcs_key)
    try:
        text = blob.download_as_text(encoding="utf-8")
    except Exception as exc:
        raise FileNotFoundError(
            f"GCS object not found or unreadable: gs://{bucket_name}/{gcs_key}"
        ) from exc
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in gs://{bucket_name}/{gcs_key}: {exc}") from exc


def _load_json_from_gcs(bucket_name: str, gcs_key: str) -> Any:
    """
    Download and JSON-parse a single object from GCS.

    Args:
        bucket_name: GCS bucket name.
        gcs_key: Full GCS object key (e.g. "linux-basics/concept_map.json").

    Returns:
        Parsed Python object.

    Raises:
        FileNotFoundError: If the object does not exist.
        ValueError: If the content cannot be parsed as JSON.
    """
    from google.cloud import storage as gcs

    client = gcs.Client()
    blob = client.bucket(bucket_name).blob(gcs_key)
    try:
        text = blob.download_as_text(encoding="utf-8")
    except Exception as exc:
        raise FileNotFoundError(
            f"GCS object not found or unreadable: gs://{bucket_name}/{gcs_key}"
        ) from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in gs://{bucket_name}/{gcs_key}: {exc}") from exc


# ---------------------------------------------------------------------------
# Source-agnostic loader (routes to GCS or local based on config)
# ---------------------------------------------------------------------------


def _load_approved_content(
    approved_dir: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """
    Load approved lesson JSON files from GCS or local filesystem.

    Routing:
        settings.gcs_pipeline_bucket non-empty  →  GCS
        otherwise                               →  local filesystem

    Args:
        approved_dir: Override for the local path. Ignored when GCS is active.
                      Defaults to ``<repo_root>/courses/linux-basics/pipeline/approved/``.

    Returns:
        Dict keyed by "{lesson_id}:{tier}", value = parsed lesson JSON dict.
    """
    from config import settings  # deferred to avoid circular import at module load

    if settings.gcs_pipeline_bucket:
        logger.info(
            "Loading approved content from GCS: gs://%s/%s/",
            settings.gcs_pipeline_bucket, _GCS_PREFIX,
        )
        return _load_from_gcs(settings.gcs_pipeline_bucket)

    if approved_dir is None:
        approved_dir = (
            Path(__file__).resolve().parent.parent
            / "courses" / "linux-basics" / "pipeline" / "approved"
        )
    logger.info("Loading approved content from local filesystem: %s", approved_dir)
    return _load_from_local(approved_dir)


# ---------------------------------------------------------------------------
# Gemini context cache internals
# ---------------------------------------------------------------------------


def _build_block_prompt(block_lessons: list[str], content: dict[str, dict[str, Any]]) -> str:
    """
    Concatenate lesson JSON for all lessons in a block into a single prompt string.
    Uses XML-style tags so the model can clearly identify lesson boundaries.

    Args:
        block_lessons: Ordered list of lesson IDs in this block (e.g. ["L01", ..., "L10"]).
        content: Full lesson content dict from _load_approved_content().

    Returns:
        A single string to be used as the cached context prompt.
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
    Create a Gemini CachedContent for one lesson block.

    Args:
        block_idx: 0, 1, or 2 — used for the cache display name.
        prompt: The full block prompt string from _build_block_prompt().

    Returns:
        google.genai.types.CachedContent handle (name field used in LessonSession).
    """
    import google.genai as genai
    from google.genai import types as genai_types

    client = genai.Client()
    cache = client.caches.create(
        model="gemini-2.5-flash",
        config=genai_types.CreateCachedContentConfig(
            contents=[prompt],
            ttl=f"{_CACHE_TTL_SECONDS}s",
            display_name=f"linux-basics-block-{block_idx}",
        ),
    )
    logger.info("block_%d cache created", block_idx, extra={"cache_name": cache.name})
    return cache


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_caches(
    approved_dir: Path | None = None,
) -> tuple[dict[str, dict[str, Any]], Any, Any]:
    """
    Load all course content at startup and optionally build Gemini context caches.

    This is the single entry point called by main.py during the FastAPI lifespan.
    It always loads lesson content regardless of ENABLE_LESSON_CACHE — the lesson
    store is needed by every session even when Gemini caching is off.

    Content loading:
        - Lesson JSON files: from GCS or local (see _load_approved_content).
        - outlines.yaml: from GCS (linux-basics/outlines.yaml) or local repo path.
        - concept_map.json: from GCS (linux-basics/concept_map.json) or local repo path.

    Gemini context caching (only when ENABLE_LESSON_CACHE=true):
        - Groups lessons into 3 blocks and creates a CachedContent per block.
        - Partial failures (one block fails) are logged but do not abort startup.

    Args:
        approved_dir: Override for the local approved/ path. Ignored when GCS is
                      active (settings.gcs_pipeline_bucket is non-empty). Useful
                      in tests to point at a tmp_path fixture.

    Returns:
        Tuple of (lesson_store, outlines, concept_map):
            lesson_store:  dict["{lesson_id}:{tier}", lesson_dict] — 87 entries in production.
            outlines:      parsed outlines.yaml (list of lesson dicts), or {} on load failure.
            concept_map:   parsed concept_map.json (dict), or {} on load failure.
    """
    global _enabled
    _enabled = False
    _cache_store.clear()

    from config import settings  # deferred

    # --- Load lesson content (always needed for the in-memory lesson store) ---
    lesson_store = _load_approved_content(approved_dir)
    if not lesson_store:
        logger.warning("No approved lesson files found — lesson store is empty")

    # --- Load outlines.yaml and concept_map.json ---
    outlines: Any = {}
    concept_map: Any = {}

    if settings.gcs_pipeline_bucket:
        # GCS path
        try:
            outlines = _load_yaml_from_gcs(
                settings.gcs_pipeline_bucket,
                f"{_GCS_PREFIX}/outlines.yaml",
            )
            n = len(outlines) if outlines else 0
            logger.info("outlines.yaml loaded from GCS (%d entries)", n)
        except Exception as exc:
            logger.error("Failed to load outlines.yaml from GCS: %s", exc, exc_info=True)
        try:
            concept_map = _load_json_from_gcs(
                settings.gcs_pipeline_bucket,
                f"{_GCS_PREFIX}/concept_map.json",
            )
            logger.info("concept_map.json loaded from GCS")
        except Exception as exc:
            logger.error("Failed to load concept_map.json from GCS: %s", exc, exc_info=True)
    else:
        # Local fallback (dev / tests)
        import yaml  # pyyaml is in dependencies

        repo_root = Path(__file__).resolve().parent.parent
        outlines_path = repo_root / "courses" / "linux-basics" / "outlines.yaml"
        concept_map_path = repo_root / "courses" / "linux-basics" / "concept_map.json"
        try:
            outlines = yaml.safe_load(outlines_path.read_text(encoding="utf-8"))
            logger.info("outlines.yaml loaded from local filesystem")
        except Exception as exc:
            logger.warning("Could not load outlines.yaml locally: %s", exc)
        try:
            concept_map = json.loads(concept_map_path.read_text(encoding="utf-8"))
            logger.info("concept_map.json loaded from local filesystem")
        except Exception as exc:
            logger.warning("Could not load concept_map.json locally: %s", exc)

    # --- Gemini context caching (optional) ---
    if os.environ.get("ENABLE_LESSON_CACHE", "false").lower() != "true":
        logger.info("Lesson cache disabled (ENABLE_LESSON_CACHE != true)")
        return lesson_store, outlines, concept_map

    if not lesson_store:
        logger.warning("Skipping Gemini cache build — lesson store is empty")
        return lesson_store, outlines, concept_map

    _enabled = True
    for block_idx, block_lessons in enumerate(_BLOCKS):
        prompt = _build_block_prompt(block_lessons, lesson_store)
        try:
            handle = _create_cache(block_idx, prompt)
            _cache_store[block_idx] = {
                "handle": handle,
                "prompt": prompt,
                "expires_at": datetime.now(tz=timezone.utc).timestamp() + _CACHE_TTL_SECONDS,
            }
        except Exception as exc:
            logger.error(
                "Failed to create cache for block_%d: %s", block_idx, exc, exc_info=True
            )

    return lesson_store, outlines, concept_map


def get_cache(lesson_id: str) -> Any | None:
    """
    Return the CachedContent handle for the block containing lesson_id,
    or None if caching is disabled, the lesson is unknown, or the handle
    has expired (lazy refresh attempted on expiry).

    Args:
        lesson_id: e.g. "L01"

    Returns:
        CachedContent handle or None.
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

    # Lazy TTL refresh
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
    """Return True if Gemini context caching is enabled and caches were built."""
    return _enabled
