"""
Unit tests for get_course_structure tool.

These tests read the actual repo files — no mocks. They verify the merge
logic, pre-computed sequencing, and the cache singleton behaviour.
"""
from __future__ import annotations

import pytest

from tools.get_course_structure import _load_course_data, get_course_structure

# ---------------------------------------------------------------------------
# Overview tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_overview_returns_all_9_modules() -> None:
    result = await get_course_structure()
    assert result["mode"] == "overview"
    assert len(result["modules"]) == 9


@pytest.mark.asyncio
async def test_overview_has_all_29_lesson_titles() -> None:
    result = await get_course_structure()
    assert len(result["lesson_titles"]) == 29


# ---------------------------------------------------------------------------
# Lesson detail tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lesson_detail_l11_correct_prerequisites() -> None:
    result = await get_course_structure(lesson_id="L11")
    assert result["mode"] == "lesson"
    assert result["prerequisites"] == ["L10"]


@pytest.mark.asyncio
async def test_lesson_detail_l11_cross_lesson_flag() -> None:
    result = await get_course_structure(lesson_id="L11")
    assert "root_user_not_yet_introduced" in result["cross_lesson_flags"]


@pytest.mark.asyncio
async def test_comes_before_comes_after() -> None:
    result = await get_course_structure(lesson_id="L09")
    assert result["comes_after"] == "L08"
    assert result["comes_before"] == "L10"


@pytest.mark.asyncio
async def test_invalid_lesson_id_returns_error() -> None:
    result = await get_course_structure(lesson_id="L99")
    assert "error" in result
    assert "L99" in result["error"]
    assert "valid_ids" in result
    assert "L01" in result["valid_ids"]
    assert "L29" in result["valid_ids"]


@pytest.mark.asyncio
async def test_l01_no_prerequisites() -> None:
    result = await get_course_structure(lesson_id="L01")
    assert result["prerequisites"] == []


# ---------------------------------------------------------------------------
# Cache singleton test
# ---------------------------------------------------------------------------


def test_cache_is_module_level_singleton() -> None:
    """After at least one call the module-level cache must be populated."""
    import tools.get_course_structure as mod

    # Ensure data is loaded (may already be loaded from earlier tests)
    _load_course_data()

    assert mod._COURSE_DATA is not None
    first = mod._COURSE_DATA

    # Call again — should return the same object, not reload
    _load_course_data()
    assert mod._COURSE_DATA is first
