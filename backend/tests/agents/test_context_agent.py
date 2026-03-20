"""
Unit tests for context_agent.py.

Tests target:
  - MODULE_CHARACTER mapping (pure data, no mocking needed)
  - read_learner_context() Firestore tool (mocked Firestore client)

The LlmAgent itself is not tested here — LLM behaviour is covered by
integration tests in Phase 4.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.context_agent import MODULE_CHARACTER, read_learner_context

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_snap(doc_id: str, data: dict[str, Any]) -> MagicMock:
    """Create a mock Firestore DocumentSnapshot."""
    snap = MagicMock()
    snap.id = doc_id
    snap.to_dict.return_value = data
    return snap


def _make_firestore_mock(
    profile_data: dict[str, Any],
    concepts_data: list[tuple[str, dict[str, Any]]],
    session_data: list[dict[str, Any]],
) -> MagicMock:
    """
    Build a mock AsyncClient that responds to the three Firestore reads in
    read_learner_context.

    concepts_data: list of (doc_id, data_dict) tuples
    session_data: list of data_dicts for session documents
    """
    mock_client = MagicMock()

    # --- Profile: db.collection("learners").document(uid).get() ---
    profile_snap = _make_snap("uid", profile_data)

    # --- Concepts: db.collection("learners").document(uid).collection("concepts").get() ---
    concept_snaps = [_make_snap(doc_id, data) for doc_id, data in concepts_data]
    concepts_coll_ref = AsyncMock()
    concepts_coll_ref.get = AsyncMock(return_value=concept_snaps)

    # --- Sessions: chained query calls ---
    session_snaps = [_make_snap(f"sess-{i}", d) for i, d in enumerate(session_data)]
    sessions_query = AsyncMock()
    sessions_query.get = AsyncMock(return_value=session_snaps)
    sessions_coll_ref = MagicMock()
    sessions_coll_ref.order_by.return_value.limit.return_value = sessions_query

    # Wire collection() calls — first two calls return the same learner document ref,
    # but we need different collection() returns for "concepts" vs "sessions"
    learner_doc_ref = MagicMock()
    learner_doc_ref.get = AsyncMock(return_value=profile_snap)

    def collection_side_effect(name: str) -> MagicMock:
        if name == "concepts":
            return concepts_coll_ref
        if name == "sessions":
            return sessions_coll_ref
        return MagicMock()

    learner_doc_ref.collection.side_effect = collection_side_effect
    mock_client.collection.return_value.document.return_value = learner_doc_ref

    return mock_client


# ---------------------------------------------------------------------------
# MODULE_CHARACTER tests (no mocking needed)
# ---------------------------------------------------------------------------


def test_module_character_mapping_complete() -> None:
    """All 9 modules have a character assigned with correct values."""
    assert len(MODULE_CHARACTER) == 9
    assert MODULE_CHARACTER[1] == "tux_jr"
    assert MODULE_CHARACTER[2] == "cursor"
    assert MODULE_CHARACTER[9] == "scrippy"
    for module_id in range(1, 10):
        assert module_id in MODULE_CHARACTER
        assert isinstance(MODULE_CHARACTER[module_id], str)
        assert len(MODULE_CHARACTER[module_id]) > 0


def test_module_character_unique() -> None:
    """Each module maps to a distinct character."""
    characters = list(MODULE_CHARACTER.values())
    assert len(characters) == len(set(characters)), "Duplicate characters found in MODULE_CHARACTER"


# ---------------------------------------------------------------------------
# read_learner_context tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_learner_context_new_learner() -> None:
    """New learner with no concepts or sessions returns correct shape with defaults."""
    mock_client = _make_firestore_mock(
        profile_data={"difficulty_tier": "beginner", "onboarding_complete": False},
        concepts_data=[],
        session_data=[],
    )
    with patch("agents.context_agent._get_firestore", return_value=mock_client):
        result = await read_learner_context("user-new-123")

    assert result["uid"] == "user-new-123"
    assert result["difficulty_tier"] == "beginner"
    assert result["onboarding_complete"] is False
    assert result["concepts"] == []
    assert result["last_session"] == {}


@pytest.mark.asyncio
async def test_read_learner_context_returns_all_concepts() -> None:
    """All concept documents from the sub-collection are included in the result."""
    now_iso = "2026-03-20T12:00:00+00:00"
    concepts_data = [
        ("L01-filesystem", {"next_review_at": now_iso, "mastery_score": 0.7}),
        ("L02-ls-command", {"next_review_at": now_iso, "mastery_score": 0.5}),
        ("L03-cd-navigation", {"next_review_at": now_iso, "mastery_score": 0.9}),
    ]
    mock_client = _make_firestore_mock(
        profile_data={"difficulty_tier": "intermediate", "onboarding_complete": True},
        concepts_data=concepts_data,
        session_data=[],
    )
    with patch("agents.context_agent._get_firestore", return_value=mock_client):
        result = await read_learner_context("user-returning-456")

    assert len(result["concepts"]) == 3
    concept_ids = {c["concept_id"] for c in result["concepts"]}
    assert "L01-filesystem" in concept_ids
    assert "L02-ls-command" in concept_ids
    assert "L03-cd-navigation" in concept_ids


@pytest.mark.asyncio
async def test_read_learner_context_converts_timestamps() -> None:
    """Firestore Timestamp objects are converted to ISO 8601 strings."""

    class FakeTimestamp:
        def isoformat(self) -> str:
            return "2026-03-20T10:00:00+00:00"

    concepts_data = [
        ("L04-chmod", {"next_review_at": FakeTimestamp(), "mastery_score": 0.6}),
    ]
    mock_client = _make_firestore_mock(
        profile_data={"difficulty_tier": "beginner", "onboarding_complete": True},
        concepts_data=concepts_data,
        session_data=[],
    )
    with patch("agents.context_agent._get_firestore", return_value=mock_client):
        result = await read_learner_context("user-ts-789")

    concept = result["concepts"][0]
    assert isinstance(concept["next_review_at"], str)
    assert concept["next_review_at"] == "2026-03-20T10:00:00+00:00"


@pytest.mark.asyncio
async def test_read_learner_context_missing_profile_uses_defaults() -> None:
    """If the learner document is empty, difficulty_tier defaults to 'beginner'."""
    mock_client = _make_firestore_mock(
        profile_data={},  # empty document
        concepts_data=[],
        session_data=[],
    )
    with patch("agents.context_agent._get_firestore", return_value=mock_client):
        result = await read_learner_context("user-empty-profile")

    assert result["difficulty_tier"] == "beginner"
    assert result["onboarding_complete"] is False


@pytest.mark.asyncio
async def test_read_learner_context_includes_last_session() -> None:
    """The most recent session document is returned in last_session."""
    session_data = [{"lesson_id": "L03", "created_at": "2026-03-19T09:00:00+00:00"}]
    mock_client = _make_firestore_mock(
        profile_data={"difficulty_tier": "intermediate", "onboarding_complete": True},
        concepts_data=[],
        session_data=session_data,
    )
    with patch("agents.context_agent._get_firestore", return_value=mock_client):
        result = await read_learner_context("user-with-session")

    assert result["last_session"]["lesson_id"] == "L03"
