"""Unit tests for search_knowledge_base tool.

All external dependencies (Vertex AI, psycopg2 pool) are mocked.
Tests reset module-level singletons before each test via autouse fixture.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from tools.search_knowledge_base import search_knowledge_base

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_singletons() -> None:
    """Reset module-level singletons so each test starts clean."""
    import tools.search_knowledge_base as skb

    skb._pool = None
    skb._embedding_model = None
    yield
    skb._pool = None
    skb._embedding_model = None


MOCK_VECTOR: list[float] = [0.1] * 768

MOCK_BEGINNER_CHUNKS: list[dict[str, Any]] = [
    {
        "chunk_id": "aaa-111",
        "lesson_id": "L01",
        "tier": "beginner",
        "content_text": "Introduction to the Linux filesystem.",
        "similarity_score": 0.95,
    },
    {
        "chunk_id": "bbb-222",
        "lesson_id": "L02",
        "tier": "beginner",
        "content_text": "Using ls to list files.",
        "similarity_score": 0.90,
    },
    {
        "chunk_id": "ccc-333",
        "lesson_id": "L03",
        "tier": "beginner",
        "content_text": "Navigating directories with cd.",
        "similarity_score": 0.85,
    },
]

MOCK_ADVANCED_CHUNKS: list[dict[str, Any]] = [
    {
        "chunk_id": "ddd-444",
        "lesson_id": "L10",
        "tier": "advanced",
        "content_text": "Advanced file permission bits and ACLs.",
        "similarity_score": 0.92,
    },
]

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_returns_top3_chunks() -> None:
    """Tool returns up to 3 chunks with all required keys."""
    with (
        patch("tools.search_knowledge_base._embed_query_sync", return_value=MOCK_VECTOR),
        patch("tools.search_knowledge_base._query_chunks_sync", return_value=MOCK_BEGINNER_CHUNKS),
    ):
        result = await search_knowledge_base("file permissions", "beginner")

    assert len(result) == 3
    required_keys = {"chunk_id", "lesson_id", "tier", "content_text", "similarity_score"}
    for chunk in result:
        assert required_keys.issubset(chunk.keys())


@pytest.mark.asyncio
async def test_tier_filter_respected() -> None:
    """Advanced tier query returns only advanced chunks."""

    def fake_query(vector: list[float], tier: str) -> list[dict[str, Any]]:
        if tier == "advanced":
            return MOCK_ADVANCED_CHUNKS
        return MOCK_BEGINNER_CHUNKS

    with (
        patch("tools.search_knowledge_base._embed_query_sync", return_value=MOCK_VECTOR),
        patch("tools.search_knowledge_base._query_chunks_sync", side_effect=fake_query),
    ):
        result = await search_knowledge_base("kernel scheduling", "advanced")

    assert len(result) == 1
    assert all(c["tier"] == "advanced" for c in result)


@pytest.mark.asyncio
async def test_empty_result_returns_empty_list() -> None:
    """Empty DB result is handled gracefully — returns []."""
    with (
        patch("tools.search_knowledge_base._embed_query_sync", return_value=MOCK_VECTOR),
        patch("tools.search_knowledge_base._query_chunks_sync", return_value=[]),
    ):
        result = await search_knowledge_base("unknown concept xyz", "intermediate")

    assert result == []


@pytest.mark.asyncio
async def test_db_error_returns_empty_list() -> None:
    """DB connection failure returns [] without raising."""
    with (
        patch("tools.search_knowledge_base._embed_query_sync", return_value=MOCK_VECTOR),
        patch(
            "tools.search_knowledge_base._query_chunks_sync",
            side_effect=Exception("Connection refused"),
        ),
    ):
        result = await search_knowledge_base("file permissions", "beginner")

    assert result == []


@pytest.mark.asyncio
async def test_embedding_error_returns_empty_list() -> None:
    """Vertex AI embedding failure returns [] without raising."""
    with patch(
        "tools.search_knowledge_base._embed_query_sync",
        side_effect=Exception("Vertex AI API error"),
    ):
        result = await search_knowledge_base("file permissions", "beginner")

    assert result == []
