"""
Unit tests for cache_manager.

All Gemini SDK calls are mocked — no network, no real GCP credentials needed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import cache_manager

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_cache_state():
    """Reset module-level state before and after every test."""
    cache_manager._cache_store.clear()
    cache_manager._enabled = False
    yield
    cache_manager._cache_store.clear()
    cache_manager._enabled = False


@pytest.fixture()
def approved_dir(tmp_path: Path) -> Path:
    """Create a minimal approved/ directory with 2 lessons × 3 tiers."""
    for tier in ("beginner", "intermediate", "advanced"):
        tier_dir = tmp_path / tier
        tier_dir.mkdir()
        for lesson_id in ("L01", "L02"):
            content = {
                "lesson_id": lesson_id,
                "tier": tier,
                "lesson": {"text": f"{lesson_id} {tier} content"},
                "quiz": {"questions": []},
            }
            (tier_dir / f"{lesson_id}.json").write_text(
                json.dumps(content), encoding="utf-8"
            )
    return tmp_path


# ---------------------------------------------------------------------------
# Cache disabled
# ---------------------------------------------------------------------------


class TestCacheDisabled:
    def test_build_caches_is_noop_when_disabled(self, approved_dir: Path) -> None:
        with patch.dict(os.environ, {"ENABLE_LESSON_CACHE": "false"}):
            cache_manager.build_caches(approved_dir)
        assert not cache_manager._enabled
        assert len(cache_manager._cache_store) == 0

    def test_get_cache_returns_none_when_disabled(self) -> None:
        assert cache_manager.get_cache("L01") is None

    def test_is_enabled_false_when_disabled(self, approved_dir: Path) -> None:
        with patch.dict(os.environ, {"ENABLE_LESSON_CACHE": "false"}):
            cache_manager.build_caches(approved_dir)
        assert cache_manager.is_enabled() is False

    def test_default_is_disabled(self) -> None:
        """ENABLE_LESSON_CACHE defaults to false — must not be set in env."""
        env = {k: v for k, v in os.environ.items() if k != "ENABLE_LESSON_CACHE"}
        with patch.dict(os.environ, env, clear=True):
            cache_manager.build_caches()
        assert not cache_manager._enabled


# ---------------------------------------------------------------------------
# Cache enabled — build
# ---------------------------------------------------------------------------


class TestCacheEnabled:
    def test_three_cache_handles_created(self, approved_dir: Path) -> None:
        mock_cache = MagicMock()
        mock_cache.name = "projects/test/cachedContents/block"
        with (
            patch.dict(os.environ, {"ENABLE_LESSON_CACHE": "true"}),
            patch("cache_manager._create_cache", return_value=mock_cache) as mock_create,
        ):
            cache_manager.build_caches(approved_dir)

        assert mock_create.call_count == 3
        assert len(cache_manager._cache_store) == 3

    def test_cache_created_for_each_block_index(self, approved_dir: Path) -> None:
        mock_cache = MagicMock()
        with (
            patch.dict(os.environ, {"ENABLE_LESSON_CACHE": "true"}),
            patch("cache_manager._create_cache", return_value=mock_cache),
        ):
            cache_manager.build_caches(approved_dir)

        assert 0 in cache_manager._cache_store
        assert 1 in cache_manager._cache_store
        assert 2 in cache_manager._cache_store

    def test_is_enabled_true_after_successful_build(self, approved_dir: Path) -> None:
        mock_cache = MagicMock()
        with (
            patch.dict(os.environ, {"ENABLE_LESSON_CACHE": "true"}),
            patch("cache_manager._create_cache", return_value=mock_cache),
        ):
            cache_manager.build_caches(approved_dir)

        assert cache_manager.is_enabled() is True

    def test_disabled_when_approved_dir_missing(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist"
        with patch.dict(os.environ, {"ENABLE_LESSON_CACHE": "true"}):
            cache_manager.build_caches(missing)
        assert not cache_manager._enabled

    def test_disabled_when_approved_dir_empty(self, tmp_path: Path) -> None:
        with patch.dict(os.environ, {"ENABLE_LESSON_CACHE": "true"}):
            cache_manager.build_caches(tmp_path)
        assert not cache_manager._enabled


# ---------------------------------------------------------------------------
# get_cache — block routing
# ---------------------------------------------------------------------------


class TestGetCache:
    def _setup_enabled(self, approved_dir: Path) -> list[MagicMock]:
        handles = [MagicMock(name=f"block_{i}") for i in range(3)]
        handle_iter = iter(handles)
        with (
            patch.dict(os.environ, {"ENABLE_LESSON_CACHE": "true"}),
            patch("cache_manager._create_cache", side_effect=lambda idx, prompt: next(handle_iter)),
        ):
            cache_manager.build_caches(approved_dir)
        return handles

    def test_l01_returns_block0_handle(self, approved_dir: Path) -> None:
        handles = self._setup_enabled(approved_dir)
        assert cache_manager.get_cache("L01") is handles[0]

    def test_l10_returns_block0_handle(self, approved_dir: Path) -> None:
        handles = self._setup_enabled(approved_dir)
        assert cache_manager.get_cache("L10") is handles[0]

    def test_l11_returns_block1_handle(self, approved_dir: Path) -> None:
        handles = self._setup_enabled(approved_dir)
        assert cache_manager.get_cache("L11") is handles[1]

    def test_l20_returns_block1_handle(self, approved_dir: Path) -> None:
        handles = self._setup_enabled(approved_dir)
        assert cache_manager.get_cache("L20") is handles[1]

    def test_l21_returns_block2_handle(self, approved_dir: Path) -> None:
        handles = self._setup_enabled(approved_dir)
        assert cache_manager.get_cache("L21") is handles[2]

    def test_l29_returns_block2_handle(self, approved_dir: Path) -> None:
        handles = self._setup_enabled(approved_dir)
        assert cache_manager.get_cache("L29") is handles[2]

    def test_unknown_lesson_returns_none(self, approved_dir: Path) -> None:
        self._setup_enabled(approved_dir)
        assert cache_manager.get_cache("L99") is None

    def test_returns_none_when_disabled(self) -> None:
        assert cache_manager.get_cache("L01") is None


# ---------------------------------------------------------------------------
# TTL expiry — lazy refresh
# ---------------------------------------------------------------------------


class TestTTLExpiry:
    def test_expired_cache_triggers_refresh(self, approved_dir: Path) -> None:
        original_handle = MagicMock(name="original")
        refreshed_handle = MagicMock(name="refreshed")

        with (
            patch.dict(os.environ, {"ENABLE_LESSON_CACHE": "true"}),
            patch("cache_manager._create_cache", return_value=original_handle),
        ):
            cache_manager.build_caches(approved_dir)

        # Force expiry by setting expires_at to the past
        for entry in cache_manager._cache_store.values():
            entry["expires_at"] = 0.0  # Unix epoch — definitely expired

        with patch("cache_manager._create_cache", return_value=refreshed_handle):
            result = cache_manager.get_cache("L01")

        assert result is refreshed_handle

    def test_valid_cache_not_refreshed(self, approved_dir: Path) -> None:
        original_handle = MagicMock(name="original")

        with (
            patch.dict(os.environ, {"ENABLE_LESSON_CACHE": "true"}),
            patch("cache_manager._create_cache", return_value=original_handle),
        ):
            cache_manager.build_caches(approved_dir)

        with patch("cache_manager._create_cache") as mock_create:
            result = cache_manager.get_cache("L01")
            mock_create.assert_not_called()

        assert result is original_handle

    def test_failed_refresh_returns_none(self, approved_dir: Path) -> None:
        original_handle = MagicMock(name="original")

        with (
            patch.dict(os.environ, {"ENABLE_LESSON_CACHE": "true"}),
            patch("cache_manager._create_cache", return_value=original_handle),
        ):
            cache_manager.build_caches(approved_dir)

        for entry in cache_manager._cache_store.values():
            entry["expires_at"] = 0.0

        with patch("cache_manager._create_cache", side_effect=RuntimeError("API down")):
            result = cache_manager.get_cache("L01")

        assert result is None


# ---------------------------------------------------------------------------
# GCS loading
# ---------------------------------------------------------------------------


class TestGcsLoading:
    """Tests for _load_from_gcs, _load_yaml_from_gcs, _load_json_from_gcs,
    and the GCS routing in build_caches."""

    def test_load_from_gcs_returns_keyed_content(self) -> None:
        """_load_from_gcs parses blobs and keys them by '{lesson_id}:{tier}'."""
        mock_blob = MagicMock()
        mock_blob.name = "linux-basics/pipeline/approved/beginner/L01.json"
        mock_blob.download_as_text.return_value = json.dumps(
            {"lesson_id": "L01", "tier": "beginner", "lesson": {}, "quiz": {}}
        )

        with patch("google.cloud.storage.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.list_blobs.return_value = [mock_blob]
            result = cache_manager._load_from_gcs("test-bucket")

        assert "L01:beginner" in result
        assert result["L01:beginner"]["lesson_id"] == "L01"

    def test_load_from_gcs_skips_non_json_blobs(self) -> None:
        """Blobs that don't end in .json are silently ignored."""
        log_blob = MagicMock()
        log_blob.name = "linux-basics/pipeline/approved/pipeline_log.txt"

        with patch("google.cloud.storage.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.list_blobs.return_value = [log_blob]
            result = cache_manager._load_from_gcs("test-bucket")

        assert len(result) == 0

    def test_load_from_gcs_skips_malformed_blob_names(self) -> None:
        """Blobs with unexpected path depth are skipped without crashing."""
        bad_blob = MagicMock()
        bad_blob.name = "linux-basics/pipeline/L01.json"  # missing tier segment

        with patch("google.cloud.storage.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.list_blobs.return_value = [bad_blob]
            result = cache_manager._load_from_gcs("test-bucket")

        assert len(result) == 0

    def test_load_from_gcs_skips_unreadable_blob(self) -> None:
        """Blobs that raise on download are logged and skipped, not re-raised."""
        bad_blob = MagicMock()
        bad_blob.name = "linux-basics/pipeline/approved/beginner/L01.json"
        bad_blob.download_as_text.side_effect = OSError("network error")

        with patch("google.cloud.storage.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.list_blobs.return_value = [bad_blob]
            result = cache_manager._load_from_gcs("test-bucket")

        assert len(result) == 0

    def test_build_caches_routes_to_gcs_when_bucket_set(self, approved_dir: Path) -> None:
        """When gcs_pipeline_bucket is non-empty, _load_from_gcs is called instead of local."""
        import config as cfg_module

        mock_settings = MagicMock()
        mock_settings.gcs_pipeline_bucket = "agentic-learning-pipeline"

        with (
            patch.dict(os.environ, {"ENABLE_LESSON_CACHE": "false"}),
            patch.object(cfg_module, "settings", mock_settings),
            patch("cache_manager._load_from_gcs", return_value={}) as mock_gcs,
            patch("cache_manager._load_from_local") as mock_local,
            patch("cache_manager._load_yaml_from_gcs", return_value={}),
            patch("cache_manager._load_json_from_gcs", return_value={}),
        ):
            cache_manager.build_caches(approved_dir)

        mock_gcs.assert_called_once_with("agentic-learning-pipeline")
        mock_local.assert_not_called()

    def test_build_caches_routes_to_local_when_no_bucket(self, approved_dir: Path) -> None:
        """When gcs_pipeline_bucket is empty, the local filesystem is used."""
        import config as cfg_module

        mock_settings = MagicMock()
        mock_settings.gcs_pipeline_bucket = ""

        with (
            patch.dict(os.environ, {"ENABLE_LESSON_CACHE": "false"}),
            patch.object(cfg_module, "settings", mock_settings),
            patch("cache_manager._load_from_gcs") as mock_gcs,
        ):
            cache_manager.build_caches(approved_dir)

        mock_gcs.assert_not_called()

    def test_build_caches_returns_tuple(self, approved_dir: Path) -> None:
        """build_caches() must return a 3-tuple (lesson_store, outlines, concept_map)."""
        with patch.dict(os.environ, {"ENABLE_LESSON_CACHE": "false"}):
            result = cache_manager.build_caches(approved_dir)

        assert isinstance(result, tuple)
        assert len(result) == 3
        lesson_store, outlines, concept_map = result
        assert isinstance(lesson_store, dict)

    def test_build_caches_lesson_store_contains_loaded_lessons(self, approved_dir: Path) -> None:
        """lesson_store returned by build_caches() contains the approved JSON content."""
        with patch.dict(os.environ, {"ENABLE_LESSON_CACHE": "false"}):
            lesson_store, _, _ = cache_manager.build_caches(approved_dir)

        # approved_dir fixture has L01 and L02 in all 3 tiers
        assert "L01:beginner" in lesson_store
        assert "L02:intermediate" in lesson_store
