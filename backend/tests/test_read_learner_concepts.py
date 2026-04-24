"""
Unit tests for main._read_learner_concepts() called directly (not via HTTP).

The conftest autouse fixture patches `main._read_learner_concepts` globally,
so to test the function body we must call it directly and handle Firestore
mocking ourselves.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _make_firestore_mock(docs: list[MagicMock]) -> MagicMock:
    """Build a mock firestore.Client whose concepts collection returns `docs`."""
    mock_collection = MagicMock()
    mock_collection.stream.return_value = docs

    mock_db = MagicMock()
    mock_db.collection.return_value.document.return_value.collection.return_value = (
        mock_collection
    )
    return mock_db


class TestReadLearnerConceptsDirect:
    def test_returns_list_of_dicts_from_firestore(self) -> None:
        """Happy path: each doc becomes a dict with lesson_id set from doc.id."""
        from main import _read_learner_concepts

        mock_doc = MagicMock()
        mock_doc.id = "L03"
        mock_doc.to_dict.return_value = {"mastery_score": 0.7, "fsrs_stability": 2.5}

        mock_db = _make_firestore_mock([mock_doc])

        with patch("google.cloud.firestore.Client", return_value=mock_db):
            result = _read_learner_concepts("uid-123")

        assert len(result) == 1
        assert result[0]["lesson_id"] == "L03"
        assert result[0]["mastery_score"] == 0.7

    def test_empty_stream_returns_empty_list(self) -> None:
        """No documents in Firestore → empty list (new learner)."""
        from main import _read_learner_concepts

        mock_db = _make_firestore_mock([])
        with patch("google.cloud.firestore.Client", return_value=mock_db):
            result = _read_learner_concepts("new-uid")

        assert result == []

    def test_to_dict_returns_none_uses_empty_dict(self) -> None:
        """doc.to_dict() returning None must be coerced to {} (not crash)."""
        from main import _read_learner_concepts

        mock_doc = MagicMock()
        mock_doc.id = "L05"
        mock_doc.to_dict.return_value = None

        mock_db = _make_firestore_mock([mock_doc])
        with patch("google.cloud.firestore.Client", return_value=mock_db):
            result = _read_learner_concepts("uid-456")

        assert len(result) == 1
        assert result[0]["lesson_id"] == "L05"

    def test_lesson_id_preserved_when_already_in_doc(self) -> None:
        """If doc already contains lesson_id, setdefault must not overwrite it."""
        from main import _read_learner_concepts

        mock_doc = MagicMock()
        mock_doc.id = "L07"
        mock_doc.to_dict.return_value = {"lesson_id": "L07-override", "mastery_score": 0.4}

        mock_db = _make_firestore_mock([mock_doc])
        with patch("google.cloud.firestore.Client", return_value=mock_db):
            result = _read_learner_concepts("uid-789")

        assert result[0]["lesson_id"] == "L07-override"

    def test_firestore_exception_returns_empty_list(self) -> None:
        """Exception from Firestore.Client constructor → [] (fallback to new learner)."""
        from main import _read_learner_concepts

        with patch(
            "google.cloud.firestore.Client",
            side_effect=Exception("connection refused"),
        ):
            result = _read_learner_concepts("uid-err")

        assert result == []

    def test_multiple_docs_all_returned(self) -> None:
        """Multiple concept documents are all included in the returned list."""
        from main import _read_learner_concepts

        docs = []
        for lesson_id in ("L01", "L02", "L03"):
            m = MagicMock()
            m.id = lesson_id
            m.to_dict.return_value = {"mastery_score": 0.5}
            docs.append(m)

        mock_db = _make_firestore_mock(docs)
        with patch("google.cloud.firestore.Client", return_value=mock_db):
            result = _read_learner_concepts("uid-multi")

        assert len(result) == 3
        lesson_ids = {r["lesson_id"] for r in result}
        assert lesson_ids == {"L01", "L02", "L03"}
