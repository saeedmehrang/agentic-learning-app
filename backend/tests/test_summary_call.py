"""
Unit tests for summary_call.run_summary().

All external I/O is mocked:
- google.genai.Client (Gemini API)
- google.cloud.firestore.Client

Tests cover the spec from PR-4:
- Session record contains all required fields with correct types.
- next_review_at is a future timestamp for a correct outcome.
- gemini_handoff_used is stored as boolean (never the prompt string).
- Firestore write called with correct paths.
- Fallback concept_outcomes derived from quiz_scores when Gemini omits them.
- Corrupt FSRS stability (<=0) is reset to default before calling run_fsrs.
- Firestore errors are swallowed (best-effort write).
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from summary_call import run_summary

# ---------------------------------------------------------------------------
# Constants / helpers
# ---------------------------------------------------------------------------

_UID = "test-uid-abc123"
_SESSION_ID = "sess-001"
_LESSON_ID = "L03"
_TIER = "intermediate"

_BASE_SESSION_DATA: dict[str, Any] = {
    "uid": _UID,
    "session_id": _SESSION_ID,
    "lesson_id": _LESSON_ID,
    "tier": _TIER,
    "quiz_scores": {
        "file_permissions": 0.1,
        "chmod_command": -0.1,
    },
    "time_on_task_seconds": 420,
    "help_triggered": False,
    "gemini_handoff_used": False,
    "concept_fsrs": {
        "file_permissions": {
            "fsrs_stability": 2.0,
            "fsrs_difficulty": 5.0,
            "mastery_score": 0.5,
        },
        "chmod_command": {
            "fsrs_stability": 1.0,
            "fsrs_difficulty": 6.0,
            "mastery_score": 0.3,
        },
    },
}

_GEMINI_RESPONSE: dict[str, Any] = {
    "summary_text": "Great work on file permissions! Review chmod next time.",
    "concept_outcomes": {
        "file_permissions": "correct",
        "chmod_command": "incorrect",
    },
}


def _make_genai_response(data: dict[str, Any]) -> MagicMock:
    mock = MagicMock()
    mock.text = json.dumps(data)
    return mock


def _patch_genai(response_data: dict[str, Any] | None = None):
    """Context manager: patches genai.Client to return a mocked generate_content."""
    if response_data is None:
        response_data = _GEMINI_RESPONSE

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = _make_genai_response(response_data)
    return patch("summary_call.genai.Client", return_value=mock_client)


def _patch_firestore():
    """Context manager: patches firestore.Client to a MagicMock."""
    return patch("summary_call.firestore.Client")


# ---------------------------------------------------------------------------
# Session record schema tests
# ---------------------------------------------------------------------------


def test_session_record_contains_all_required_fields() -> None:
    """run_summary returns a dict with all schema-required fields."""
    with _patch_genai(), _patch_firestore():
        result = run_summary(_BASE_SESSION_DATA.copy())

    required_fields = {
        "session_id",
        "uid",
        "lesson_id",
        "tier",
        "quiz_scores",
        "time_on_task_seconds",
        "help_triggered",
        "gemini_handoff_used",
        "summary_text",
        "concept_outcomes",
        "fsrs_results",
        "completed_at",
    }
    assert required_fields.issubset(result.keys()), (
        f"Missing fields: {required_fields - result.keys()}"
    )


def test_session_record_field_types() -> None:
    """Field types in the session record match the Firestore schema."""
    with _patch_genai(), _patch_firestore():
        result = run_summary(_BASE_SESSION_DATA.copy())

    assert isinstance(result["session_id"], str)
    assert isinstance(result["uid"], str)
    assert isinstance(result["lesson_id"], str)
    assert isinstance(result["tier"], str)
    assert isinstance(result["quiz_scores"], dict)
    assert isinstance(result["time_on_task_seconds"], int)
    assert isinstance(result["help_triggered"], bool)
    assert isinstance(result["gemini_handoff_used"], bool)
    assert isinstance(result["summary_text"], str)
    assert isinstance(result["concept_outcomes"], dict)
    assert isinstance(result["fsrs_results"], dict)
    assert isinstance(result["completed_at"], str)


def test_session_record_values_match_input() -> None:
    """Passthrough fields are written unchanged from session_data."""
    with _patch_genai(), _patch_firestore():
        result = run_summary(_BASE_SESSION_DATA.copy())

    assert result["uid"] == _UID
    assert result["session_id"] == _SESSION_ID
    assert result["lesson_id"] == _LESSON_ID
    assert result["tier"] == _TIER
    assert result["time_on_task_seconds"] == 420
    assert result["help_triggered"] is False
    assert result["gemini_handoff_used"] is False


# ---------------------------------------------------------------------------
# gemini_handoff_used is always boolean
# ---------------------------------------------------------------------------


def test_gemini_handoff_used_is_boolean_when_true() -> None:
    """gemini_handoff_used=True is stored as bool True, never a string."""
    data = {**_BASE_SESSION_DATA, "gemini_handoff_used": True}
    with _patch_genai(), _patch_firestore():
        result = run_summary(data)

    assert result["gemini_handoff_used"] is True
    assert type(result["gemini_handoff_used"]) is bool


def test_gemini_handoff_used_is_boolean_when_false() -> None:
    """gemini_handoff_used=False is stored as bool False."""
    data = {**_BASE_SESSION_DATA, "gemini_handoff_used": False}
    with _patch_genai(), _patch_firestore():
        result = run_summary(data)

    assert result["gemini_handoff_used"] is False
    assert type(result["gemini_handoff_used"]) is bool


def test_gemini_handoff_used_coerced_from_truthy_string() -> None:
    """Truthy non-bool (e.g. a non-empty string) is coerced to bool."""
    data = {**_BASE_SESSION_DATA, "gemini_handoff_used": "some-prompt-text"}
    with _patch_genai(), _patch_firestore():
        result = run_summary(data)

    assert type(result["gemini_handoff_used"]) is bool
    assert result["gemini_handoff_used"] is True


# ---------------------------------------------------------------------------
# next_review_at is in the future for a correct outcome
# ---------------------------------------------------------------------------


def test_next_review_at_is_future_timestamp_for_correct_outcome() -> None:
    """FSRS next_review_at for a correct answer must be strictly in the future."""
    before = datetime.now(UTC)
    with _patch_genai(), _patch_firestore():
        result = run_summary(_BASE_SESSION_DATA.copy())

    fsrs = result["fsrs_results"]
    assert "file_permissions" in fsrs, "file_permissions concept should have FSRS result"

    next_review_str: str = fsrs["file_permissions"]["next_review_at"]
    next_review = datetime.fromisoformat(next_review_str)
    assert next_review.tzinfo is not None
    assert next_review > before, (
        f"next_review_at={next_review} should be after test start {before}"
    )


def test_next_review_at_is_future_for_incorrect_outcome() -> None:
    """next_review_at is also in the future for incorrect (stability resets to 1.0)."""
    before = datetime.now(UTC)
    with _patch_genai(), _patch_firestore():
        result = run_summary(_BASE_SESSION_DATA.copy())

    fsrs = result["fsrs_results"]
    assert "chmod_command" in fsrs

    next_review = datetime.fromisoformat(fsrs["chmod_command"]["next_review_at"])
    assert next_review > before


# ---------------------------------------------------------------------------
# FSRS values reflect correct/incorrect outcomes
# ---------------------------------------------------------------------------


def test_fsrs_correct_doubles_stability() -> None:
    """Correct outcome doubles the prior stability."""
    prior_stability = _BASE_SESSION_DATA["concept_fsrs"]["file_permissions"]["fsrs_stability"]
    with _patch_genai(), _patch_firestore():
        result = run_summary(_BASE_SESSION_DATA.copy())

    new_stability = result["fsrs_results"]["file_permissions"]["fsrs_stability"]
    assert new_stability == pytest.approx(prior_stability * 2.0)


def test_fsrs_incorrect_resets_stability() -> None:
    """Incorrect outcome resets stability to 1.0."""
    with _patch_genai(), _patch_firestore():
        result = run_summary(_BASE_SESSION_DATA.copy())

    new_stability = result["fsrs_results"]["chmod_command"]["fsrs_stability"]
    assert new_stability == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Fallback: concept_outcomes derived from quiz_scores
# ---------------------------------------------------------------------------


def test_fallback_concept_outcomes_from_quiz_scores() -> None:
    """When Gemini omits concept_outcomes, they are derived from quiz_scores."""
    gemini_response_no_outcomes = {
        "summary_text": "Well done!",
        "concept_outcomes": {},  # empty — triggers fallback
    }
    with _patch_genai(gemini_response_no_outcomes), _patch_firestore():
        result = run_summary(_BASE_SESSION_DATA.copy())

    # quiz_scores: file_permissions=+0.1 (correct), chmod_command=-0.1 (incorrect)
    outcomes = result["concept_outcomes"]
    assert outcomes["file_permissions"] == "correct"
    assert outcomes["chmod_command"] == "incorrect"


# ---------------------------------------------------------------------------
# Corrupt FSRS stability guard
# ---------------------------------------------------------------------------


def test_corrupt_fsrs_stability_reset_to_default() -> None:
    """Stability <= 0 in concept_fsrs does not raise — it is reset to default."""
    data = dict(_BASE_SESSION_DATA)
    data["concept_fsrs"] = {
        "file_permissions": {
            "fsrs_stability": 0.0,   # corrupt
            "fsrs_difficulty": 5.0,
            "mastery_score": 0.5,
        }
    }
    gemini_response = {
        "summary_text": "Keep going!",
        "concept_outcomes": {"file_permissions": "correct"},
    }
    with _patch_genai(gemini_response), _patch_firestore():
        result = run_summary(data)

    # Should not raise; FSRS result should exist and next_review_at in future
    assert "file_permissions" in result["fsrs_results"]
    before = datetime.now(UTC)
    nra = datetime.fromisoformat(result["fsrs_results"]["file_permissions"]["next_review_at"])
    assert nra > before


def test_missing_concept_fsrs_uses_defaults() -> None:
    """Concepts absent from concept_fsrs use default stability/difficulty/mastery."""
    data = {**_BASE_SESSION_DATA, "concept_fsrs": {}}  # no prior FSRS state
    with _patch_genai(), _patch_firestore():
        result = run_summary(data)

    for concept_id in _GEMINI_RESPONSE["concept_outcomes"]:
        assert concept_id in result["fsrs_results"]


# ---------------------------------------------------------------------------
# Firestore write paths
# ---------------------------------------------------------------------------


def test_firestore_session_record_written_to_correct_path() -> None:
    """Session record is written to learners/{uid}/sessions/{session_id}."""
    sessions_doc_mock = MagicMock()
    concepts_doc_mock = MagicMock()

    def _collection_side_effect(name: str) -> MagicMock:
        col = MagicMock()
        if name == "sessions":
            col.document.return_value = sessions_doc_mock
        elif name == "concepts":
            col.document.return_value = concepts_doc_mock
        return col

    with _patch_genai():
        with patch("summary_call.firestore.Client") as mock_fs_cls:
            mock_db = MagicMock()
            mock_fs_cls.return_value = mock_db
            learner_doc = MagicMock()
            learner_doc.collection.side_effect = _collection_side_effect
            learners_col = MagicMock()
            learners_col.document.return_value = learner_doc
            mock_db.collection.return_value = learners_col

            run_summary(_BASE_SESSION_DATA.copy())

    sessions_doc_mock.set.assert_called_once()
    # Verify the session record payload contains the expected keys
    payload = sessions_doc_mock.set.call_args[0][0]
    assert payload["session_id"] == _SESSION_ID
    assert payload["uid"] == _UID
    assert payload["lesson_id"] == _LESSON_ID


def test_firestore_concepts_written_to_correct_path() -> None:
    """FSRS updates are merged into learners/{uid}/concepts/{lesson_id}."""
    sessions_doc_mock = MagicMock()
    concepts_doc_mock = MagicMock()

    def _collection_side_effect(name: str) -> MagicMock:
        col = MagicMock()
        if name == "sessions":
            col.document.return_value = sessions_doc_mock
        elif name == "concepts":
            col.document.return_value = concepts_doc_mock
        return col

    with _patch_genai():
        with patch("summary_call.firestore.Client") as mock_fs_cls:
            mock_db = MagicMock()
            mock_fs_cls.return_value = mock_db
            learner_doc = MagicMock()
            learner_doc.collection.side_effect = _collection_side_effect
            learners_col = MagicMock()
            learners_col.document.return_value = learner_doc
            mock_db.collection.return_value = learners_col

            run_summary(_BASE_SESSION_DATA.copy())

    concepts_doc_mock.set.assert_called_once()
    _, kwargs = concepts_doc_mock.set.call_args
    assert kwargs.get("merge") is True


def test_firestore_concept_update_has_required_fields() -> None:
    """Each concept in the Firestore update contains the required FSRS fields."""
    sessions_doc_mock = MagicMock()
    concepts_doc_mock = MagicMock()

    def _collection_side_effect(name: str) -> MagicMock:
        col = MagicMock()
        if name == "sessions":
            col.document.return_value = sessions_doc_mock
        elif name == "concepts":
            col.document.return_value = concepts_doc_mock
        return col

    with _patch_genai():
        with patch("summary_call.firestore.Client") as mock_fs_cls:
            mock_db = MagicMock()
            mock_fs_cls.return_value = mock_db
            learner_doc = MagicMock()
            learner_doc.collection.side_effect = _collection_side_effect
            learners_col = MagicMock()
            learners_col.document.return_value = learner_doc
            mock_db.collection.return_value = learners_col

            run_summary(_BASE_SESSION_DATA.copy())

    concept_update_payload = concepts_doc_mock.set.call_args[0][0]

    for concept_id in _GEMINI_RESPONSE["concept_outcomes"]:
        assert concept_id in concept_update_payload
        entry = concept_update_payload[concept_id]
        for field in ("fsrs_stability", "fsrs_difficulty", "mastery_score",
                      "next_review_at", "last_reviewed_at"):
            assert field in entry, f"Missing field {field!r} for concept {concept_id!r}"


# ---------------------------------------------------------------------------
# Firestore errors are swallowed
# ---------------------------------------------------------------------------


def test_firestore_error_does_not_raise() -> None:
    """Firestore errors are logged but do not propagate to the caller."""
    with _patch_genai():
        with patch("summary_call.firestore.Client") as mock_fs_cls:
            mock_fs_cls.side_effect = Exception("Firestore unavailable")
            # Should not raise
            result = run_summary(_BASE_SESSION_DATA.copy())

    # The summary result is still returned
    assert "summary_text" in result
    assert "fsrs_results" in result


# ---------------------------------------------------------------------------
# completed_at is a valid ISO 8601 UTC string
# ---------------------------------------------------------------------------


def test_completed_at_is_valid_iso8601_utc() -> None:
    """completed_at is parseable as an ISO 8601 datetime with timezone info."""
    before = datetime.now(UTC)
    with _patch_genai(), _patch_firestore():
        result = run_summary(_BASE_SESSION_DATA.copy())
    after = datetime.now(UTC)

    completed_at = datetime.fromisoformat(result["completed_at"])
    assert completed_at.tzinfo is not None
    assert before <= completed_at <= after


# ---------------------------------------------------------------------------
# summary_text propagated from Gemini
# ---------------------------------------------------------------------------


def test_summary_text_from_gemini_response() -> None:
    """summary_text in the record matches what Gemini returned."""
    with _patch_genai(), _patch_firestore():
        result = run_summary(_BASE_SESSION_DATA.copy())

    assert result["summary_text"] == _GEMINI_RESPONSE["summary_text"]


# ---------------------------------------------------------------------------
# session_id auto-generated when absent
# ---------------------------------------------------------------------------


def test_session_id_auto_generated_when_missing() -> None:
    """run_summary generates a session_id UUID if not provided in session_data."""
    data = {k: v for k, v in _BASE_SESSION_DATA.items() if k != "session_id"}
    with _patch_genai(), _patch_firestore():
        result = run_summary(data)

    assert isinstance(result["session_id"], str)
    assert len(result["session_id"]) > 0


# ---------------------------------------------------------------------------
# _require_text — None text path (line 98)
# ---------------------------------------------------------------------------


def test_require_text_raises_when_response_text_is_none() -> None:
    """_require_text must raise ValueError when response.text is None."""
    from summary_call import _require_text
    mock_response = MagicMock()
    mock_response.text = None
    with pytest.raises(ValueError, match="Gemini returned no text"):
        _require_text(mock_response, "test_context")


# ---------------------------------------------------------------------------
# _extract_json — markdown fence stripping and error paths (lines 107-119)
# ---------------------------------------------------------------------------


def test_extract_json_strips_markdown_fence() -> None:
    """_extract_json must strip ```json ... ``` fences before parsing."""
    from summary_call import _extract_json
    text = '```json\n{"summary_text": "Hello"}\n```'
    result = _extract_json(text)
    assert result == {"summary_text": "Hello"}


def test_extract_json_strips_plain_fence() -> None:
    from summary_call import _extract_json
    text = '```\n{"summary_text": "Hello"}\n```'
    result = _extract_json(text)
    assert result == {"summary_text": "Hello"}


def test_extract_json_raises_when_no_json_found() -> None:
    """_extract_json must raise ValueError when no JSON object is found."""
    from summary_call import _extract_json
    with pytest.raises(ValueError, match="No JSON object found"):
        _extract_json("This is not JSON at all.")


def test_extract_json_raises_on_invalid_json() -> None:
    """_extract_json must raise ValueError when the JSON is malformed."""
    from summary_call import _extract_json
    with pytest.raises(ValueError, match="Failed to parse JSON"):
        _extract_json("{invalid json}")


# ---------------------------------------------------------------------------
# run_summary — Gemini call exception (lines 178-181)
# ---------------------------------------------------------------------------


def test_run_summary_re_raises_on_gemini_exception() -> None:
    """Lines 178-181: exception from generate_content must propagate after logging."""
    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = RuntimeError("API down")
    with (
        patch("summary_call.genai.Client", return_value=mock_client),
        _patch_firestore(),
    ):
        with pytest.raises(RuntimeError, match="API down"):
            run_summary(_BASE_SESSION_DATA.copy())


# ---------------------------------------------------------------------------
# run_summary — run_fsrs() ValueError is swallowed (lines 212-213)
# ---------------------------------------------------------------------------


def test_run_summary_skips_concept_when_fsrs_raises_value_error() -> None:
    """Lines 212-213: run_fsrs ValueError for a concept must be swallowed — other concepts proceed."""
    from unittest.mock import call

    call_count: list[int] = [0]

    def _fsrs_side_effect(concept_id: str, *args: object, **kwargs: object) -> dict:
        call_count[0] += 1
        if concept_id == "chmod_command":
            raise ValueError("bad FSRS input")
        return {
            "mastery_score": 0.6,
            "fsrs_stability": 3.0,
            "fsrs_difficulty": 5.0,
            "next_review_at": "2099-01-01T00:00:00+00:00",
            "last_review_at": "2026-01-01T00:00:00+00:00",
        }

    with (
        _patch_genai(),
        _patch_firestore(),
        patch("summary_call.run_fsrs", side_effect=_fsrs_side_effect),
    ):
        result = run_summary(_BASE_SESSION_DATA.copy())

    # file_permissions was processed; chmod_command was skipped — no crash
    assert "summary_text" in result
    # At least one concept was processed (file_permissions)
    assert call_count[0] >= 1
