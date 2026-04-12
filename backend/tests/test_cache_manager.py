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
