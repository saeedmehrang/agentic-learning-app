"""
Edge-case and robustness tests for cache_manager.

Covers partial build failures, prompt structure, malformed files,
double-call rebuild, and the boundary between block_0 and block_1.
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
def _reset():
    cache_manager._cache_store.clear()
    cache_manager._enabled = False
    yield
    cache_manager._cache_store.clear()
    cache_manager._enabled = False


@pytest.fixture()
def approved_dir(tmp_path: Path) -> Path:
    for tier in ("beginner", "intermediate", "advanced"):
        d = tmp_path / tier
        d.mkdir()
        for n in range(1, 5):
            lid = f"L{n:02d}"
            (d / f"{lid}.json").write_text(
                json.dumps({"lesson_id": lid, "tier": tier, "lesson": {}, "quiz": {}}),
                encoding="utf-8",
            )
    return tmp_path


# ---------------------------------------------------------------------------
# _build_block_prompt structure
# ---------------------------------------------------------------------------


class TestBlockPromptStructure:
    def test_prompt_contains_course_content_tags(self, approved_dir: Path) -> None:
        content = cache_manager._load_approved_files(approved_dir)
        prompt = cache_manager._build_block_prompt(["L01", "L02"], content)
        assert "<course_content>" in prompt
        assert "</course_content>" in prompt

    def test_prompt_contains_lesson_tags_for_all_tiers(self, approved_dir: Path) -> None:
        content = cache_manager._load_approved_files(approved_dir)
        prompt = cache_manager._build_block_prompt(["L01"], content)
        for tier in ("beginner", "intermediate", "advanced"):
            assert f'<lesson id="L01" tier="{tier}">' in prompt

    def test_prompt_contains_valid_json_per_lesson(self, approved_dir: Path) -> None:
        content = cache_manager._load_approved_files(approved_dir)
        prompt = cache_manager._build_block_prompt(["L01"], content)
        # Each lesson block must contain parseable JSON
        import re
        matches = re.findall(r'<lesson[^>]+>(.*?)</lesson>', prompt, re.DOTALL)
        assert len(matches) > 0
        for match in matches:
            json.loads(match.strip())  # raises if invalid

    def test_prompt_excludes_lessons_not_in_block(self, approved_dir: Path) -> None:
        content = cache_manager._load_approved_files(approved_dir)
        prompt = cache_manager._build_block_prompt(["L01"], content)
        assert 'id="L02"' not in prompt
        assert 'id="L03"' not in prompt

    def test_empty_block_lessons_returns_wrapper_only(self) -> None:
        prompt = cache_manager._build_block_prompt([], {})
        assert "<course_content>" in prompt
        assert "</course_content>" in prompt
        assert "<lesson" not in prompt


# ---------------------------------------------------------------------------
# Malformed files in approved dir
# ---------------------------------------------------------------------------


class TestMalformedApprovedFiles:
    def test_invalid_json_file_is_skipped(self, tmp_path: Path) -> None:
        d = tmp_path / "beginner"
        d.mkdir()
        (d / "L01.json").write_text("{ not valid json", encoding="utf-8")
        (d / "L02.json").write_text(
            json.dumps({"lesson_id": "L02"}), encoding="utf-8"
        )
        content = cache_manager._load_approved_files(tmp_path)
        assert "L01:beginner" not in content
        assert "L02:beginner" in content

    def test_unreadable_file_is_skipped(self, tmp_path: Path) -> None:
        """OSError on file read must not crash the loader."""
        d = tmp_path / "beginner"
        d.mkdir()
        f = d / "L01.json"
        f.write_text(json.dumps({"lesson_id": "L01"}), encoding="utf-8")
        f.chmod(0o000)  # make unreadable
        try:
            content = cache_manager._load_approved_files(tmp_path)
            assert "L01:beginner" not in content
        finally:
            f.chmod(0o644)  # restore for cleanup

    def test_non_json_files_are_ignored(self, tmp_path: Path) -> None:
        d = tmp_path / "beginner"
        d.mkdir()
        (d / "notes.txt").write_text("not a lesson", encoding="utf-8")
        (d / "L01.json").write_text(json.dumps({"lesson_id": "L01"}), encoding="utf-8")
        content = cache_manager._load_approved_files(tmp_path)
        assert "L01:beginner" in content
        assert len(content) == 1


# ---------------------------------------------------------------------------
# Partial build failure
# ---------------------------------------------------------------------------


class TestPartialBuildFailure:
    def test_partial_failure_does_not_disable_cache(self, approved_dir: Path) -> None:
        """If block_1 fails, block_0 and block_2 should still be available."""
        good_handle = MagicMock(name="good")

        def side_effect(block_idx: int, prompt: str) -> MagicMock:
            if block_idx == 1:
                raise RuntimeError("block_1 API error")
            return good_handle

        with (
            patch.dict(os.environ, {"ENABLE_LESSON_CACHE": "true"}),
            patch("cache_manager._create_cache", side_effect=side_effect),
        ):
            cache_manager.build_caches(approved_dir)

        assert cache_manager.is_enabled() is True
        assert cache_manager.get_cache("L01") is good_handle   # block_0
        assert cache_manager.get_cache("L11") is None           # block_1 failed
        assert cache_manager.get_cache("L21") is good_handle   # block_2

    def test_all_blocks_fail_sets_enabled_but_store_empty(self, approved_dir: Path) -> None:
        """All blocks failing: _enabled=True but every get_cache returns None."""
        with (
            patch.dict(os.environ, {"ENABLE_LESSON_CACHE": "true"}),
            patch("cache_manager._create_cache", side_effect=RuntimeError("API down")),
        ):
            cache_manager.build_caches(approved_dir)

        assert cache_manager.is_enabled() is True
        assert cache_manager.get_cache("L01") is None
        assert cache_manager.get_cache("L15") is None
        assert cache_manager.get_cache("L25") is None


# ---------------------------------------------------------------------------
# Double-call rebuild
# ---------------------------------------------------------------------------


class TestDoubleBuildCall:
    def test_second_build_replaces_first_handles(self, approved_dir: Path) -> None:
        handle_v1 = MagicMock(name="v1")
        handle_v2 = MagicMock(name="v2")
        calls = {"count": 0}

        def side_effect(block_idx: int, prompt: str) -> MagicMock:
            calls["count"] += 1
            return handle_v1 if calls["count"] <= 3 else handle_v2

        with (
            patch.dict(os.environ, {"ENABLE_LESSON_CACHE": "true"}),
            patch("cache_manager._create_cache", side_effect=side_effect),
        ):
            cache_manager.build_caches(approved_dir)
            assert cache_manager.get_cache("L01") is handle_v1

            cache_manager.build_caches(approved_dir)
            assert cache_manager.get_cache("L01") is handle_v2

    def test_second_build_clears_old_entries(self, approved_dir: Path) -> None:
        with (
            patch.dict(os.environ, {"ENABLE_LESSON_CACHE": "true"}),
            patch("cache_manager._create_cache", return_value=MagicMock()),
        ):
            cache_manager.build_caches(approved_dir)

            # Second call with cache disabled — must clear the store
        with patch.dict(os.environ, {"ENABLE_LESSON_CACHE": "false"}):
            cache_manager.build_caches(approved_dir)

        assert len(cache_manager._cache_store) == 0
        assert not cache_manager.is_enabled()


# ---------------------------------------------------------------------------
# Block boundary correctness
# ---------------------------------------------------------------------------


class TestBlockBoundaries:
    @pytest.mark.parametrize("lesson_id,expected_block", [
        ("L01", 0), ("L10", 0),
        ("L11", 1), ("L20", 1),
        ("L21", 2), ("L29", 2),
    ])
    def test_lesson_in_correct_block(
        self, lesson_id: str, expected_block: int, approved_dir: Path
    ) -> None:
        assert cache_manager._LESSON_BLOCK.get(lesson_id) == expected_block

    def test_all_29_lessons_have_block_assignment(self) -> None:
        expected = {f"L{i:02d}" for i in range(1, 30)}
        assert set(cache_manager._LESSON_BLOCK.keys()) == expected

    def test_no_lesson_assigned_to_block_3_or_higher(self) -> None:
        assert all(v <= 2 for v in cache_manager._LESSON_BLOCK.values())
