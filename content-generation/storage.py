"""
storage.py — Transparent local/GCS storage backend for the content pipeline.

All pipeline scripts route file I/O through a StorageBackend so the same code
runs unchanged on a local dev machine and on Cloud Run (where the filesystem is
ephemeral).

Backend selection is automatic:
  - GCS_PIPELINE_BUCKET unset or empty  →  LocalBackend  (pathlib)
  - GCS_PIPELINE_BUCKET=<bucket-name>   →  GcsBackend    (google-cloud-storage)

All paths passed to the backend are relative strings such as:
    "pipeline/approved/beginner/L01.json"
    "pipeline/pipeline_log.json"

LocalBackend resolves these under:
    <repo-root>/courses/linux-basics/

GcsBackend resolves them as GCS object keys under:
    linux-basics/

Usage
-----
    from storage import get_storage_backend

    storage = get_storage_backend()          # call once at module level

    if storage.exists("pipeline/approved/beginner/L01.json"):
        data = storage.read_json("pipeline/approved/beginner/L01.json")

    storage.write_json("pipeline/approved/beginner/L01.json", data)

    for rel_path in storage.list_prefix("pipeline/approved/"):
        ...  # e.g. "pipeline/approved/beginner/L01.json"

    print(storage.location)  # "gs://bucket/linux-basics/" or "/path/to/courses/linux-basics/"
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Root of the local pipeline outputs, relative to repo root.
_COURSE_SUBPATH = "courses/linux-basics"

# GCS key prefix — all pipeline objects live under this prefix in the bucket.
_GCS_PREFIX = "linux-basics"

_REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class StorageBackend(ABC):
    """Abstract interface for pipeline file I/O."""

    @property
    @abstractmethod
    def location(self) -> str:
        """Human-readable root location string for log messages."""

    @abstractmethod
    def exists(self, rel_path: str) -> bool:
        """Return True if the object/file at rel_path exists."""

    @abstractmethod
    def read_json(self, rel_path: str) -> Any:
        """Read and parse a JSON file. Raises FileNotFoundError or ValueError on failure."""

    @abstractmethod
    def write_json(self, rel_path: str, data: Any) -> None:
        """Serialise data as JSON and write to rel_path. Creates parent dirs/prefixes as needed."""

    @abstractmethod
    def write_text(self, rel_path: str, text: str) -> None:
        """Write a plain-text string to rel_path."""

    @abstractmethod
    def list_prefix(self, prefix: str) -> list[str]:
        """
        Return sorted relative path strings for all objects under prefix.
        prefix should end with '/'.
        Example: list_prefix("pipeline/approved/") ->
            ["pipeline/approved/advanced/L01.json", "pipeline/approved/beginner/L01.json", ...]
        """


# ---------------------------------------------------------------------------
# Local backend
# ---------------------------------------------------------------------------


class LocalBackend(StorageBackend):
    """Filesystem-backed storage rooted at <repo-root>/courses/linux-basics/."""

    def __init__(self) -> None:
        self._root = _REPO_ROOT / _COURSE_SUBPATH

    @property
    def location(self) -> str:
        return str(self._root) + "/"

    def _resolve(self, rel_path: str) -> Path:
        return self._root / rel_path

    def exists(self, rel_path: str) -> bool:
        return self._resolve(rel_path).exists()

    def read_json(self, rel_path: str) -> Any:
        path = self._resolve(rel_path)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise
        except Exception as exc:
            raise ValueError(f"Failed to read JSON from {path}: {exc}") from exc

    def write_json(self, rel_path: str, data: Any) -> None:
        path = self._resolve(rel_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            raise OSError(f"Failed to write JSON to {path}: {exc}") from exc

    def write_text(self, rel_path: str, text: str) -> None:
        path = self._resolve(rel_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text(text, encoding="utf-8")
        except OSError as exc:
            raise OSError(f"Failed to write text to {path}: {exc}") from exc

    def list_prefix(self, prefix: str) -> list[str]:
        base = self._resolve(prefix)
        if not base.exists():
            return []
        return sorted(
            str(p.relative_to(self._root))
            for p in base.rglob("*.json")
            if p.is_file()
        )


# ---------------------------------------------------------------------------
# GCS backend
# ---------------------------------------------------------------------------


class GcsBackend(StorageBackend):
    """GCS-backed storage. Objects live at gs://<bucket>/linux-basics/<rel_path>."""

    def __init__(self, bucket_name: str) -> None:
        # Lazy import — google-cloud-storage is only installed when the gcs
        # extra is present (i.e. on Cloud Run). This avoids an ImportError on
        # local dev where the package is not installed.
        try:
            from google.cloud import storage as gcs  # ty: ignore[unresolved-import]
        except ImportError as exc:
            raise ImportError(
                "google-cloud-storage is not installed. "
                "Install it with: pip install '.[gcs]'"
            ) from exc

        self._client = gcs.Client()
        self._bucket = self._client.bucket(bucket_name)
        self._bucket_name = bucket_name

    @property
    def location(self) -> str:
        return f"gs://{self._bucket_name}/{_GCS_PREFIX}/"

    def _key(self, rel_path: str) -> str:
        """Build the full GCS object key from a relative path."""
        return f"{_GCS_PREFIX}/{rel_path}"

    def exists(self, rel_path: str) -> bool:
        blob = self._bucket.blob(self._key(rel_path))
        return blob.exists()

    def read_json(self, rel_path: str) -> Any:
        blob = self._bucket.blob(self._key(rel_path))
        try:
            text = blob.download_as_text(encoding="utf-8")
        except Exception as exc:
            raise FileNotFoundError(
                f"GCS object not found or unreadable: {self.location}{rel_path}"
            ) from exc
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSON in GCS object {self.location}{rel_path}: {exc}"
            ) from exc

    def write_json(self, rel_path: str, data: Any) -> None:
        blob = self._bucket.blob(self._key(rel_path))
        try:
            blob.upload_from_string(
                json.dumps(data, indent=2, ensure_ascii=False),
                content_type="application/json",
            )
        except Exception as exc:
            raise OSError(
                f"Failed to write JSON to {self.location}{rel_path}: {exc}"
            ) from exc

    def write_text(self, rel_path: str, text: str) -> None:
        blob = self._bucket.blob(self._key(rel_path))
        try:
            blob.upload_from_string(text, content_type="text/plain; charset=utf-8")
        except Exception as exc:
            raise OSError(
                f"Failed to write text to {self.location}{rel_path}: {exc}"
            ) from exc

    def list_prefix(self, prefix: str) -> list[str]:
        full_prefix = self._key(prefix)
        blobs = self._client.list_blobs(self._bucket_name, prefix=full_prefix)
        results: list[str] = []
        key_prefix = f"{_GCS_PREFIX}/"
        for blob in blobs:
            if blob.name.endswith(".json"):
                # Strip the "linux-basics/" prefix to get the relative path
                rel = blob.name[len(key_prefix):]
                results.append(rel)
        return sorted(results)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_storage_backend() -> StorageBackend:
    """
    Return the appropriate StorageBackend based on environment.

    Uses GcsBackend when settings.gcs_pipeline_bucket is non-empty,
    otherwise LocalBackend. Import is deferred to avoid a circular import
    with config.py at module load time.
    """
    from config import settings  # local import to avoid circular dependency

    if settings.gcs_pipeline_bucket:
        logger.debug(
            "[storage] Using GCS backend: gs://%s/%s/",
            settings.gcs_pipeline_bucket, _GCS_PREFIX,
        )
        return GcsBackend(settings.gcs_pipeline_bucket)

    logger.debug("[storage] Using local backend: %s/%s/", _REPO_ROOT, _COURSE_SUBPATH)
    return LocalBackend()
