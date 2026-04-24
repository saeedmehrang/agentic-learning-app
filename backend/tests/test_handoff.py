"""
Unit tests for backend/handoff.py — provider pattern, URL construction, truncation.
All tests run without network access or GCP credentials.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from handoff import (
    MAX_PROMPT_CHARS,
    DisabledProvider,
    GoogleAiStudioProvider,
    _truncate,
    get_handoff_provider,
)

SHORT_PROMPT = "I am learning Linux. Please help me understand file permissions."
LESSON_ID = "L07"


# ---------------------------------------------------------------------------
# _truncate
# ---------------------------------------------------------------------------


class TestTruncate:
    def test_short_prompt_returned_unchanged(self) -> None:
        result = _truncate(SHORT_PROMPT, LESSON_ID)
        assert result == SHORT_PROMPT

    def test_prompt_at_exact_limit_not_truncated(self) -> None:
        prompt = "x" * MAX_PROMPT_CHARS
        result = _truncate(prompt, LESSON_ID)
        assert result == prompt

    def test_long_prompt_truncated_at_period(self) -> None:
        body = ("This is a sentence. " * 200)[:MAX_PROMPT_CHARS + 100]
        result = _truncate(body, LESSON_ID)
        assert len(result) <= MAX_PROMPT_CHARS
        assert result.endswith(f" [see lesson {LESSON_ID} for full context]")
        # Truncation point must be a sentence boundary
        suffix = f" [see lesson {LESSON_ID} for full context]"
        text_part = result[: -len(suffix)]
        assert text_part.endswith(".")

    def test_long_prompt_truncated_at_question_mark(self) -> None:
        body = ("Did you understand? " * 200)[:MAX_PROMPT_CHARS + 100]
        result = _truncate(body, LESSON_ID)
        suffix = f" [see lesson {LESSON_ID} for full context]"
        text_part = result[: -len(suffix)]
        assert text_part.endswith("?")

    def test_long_prompt_truncated_at_exclamation(self) -> None:
        body = ("Great job! " * 200)[:MAX_PROMPT_CHARS + 100]
        result = _truncate(body, LESSON_ID)
        suffix = f" [see lesson {LESSON_ID} for full context]"
        text_part = result[: -len(suffix)]
        assert text_part.endswith("!")

    def test_long_prompt_no_sentence_boundary_truncates_at_cutoff(self) -> None:
        # No punctuation — should cut at the char boundary
        body = "x" * (MAX_PROMPT_CHARS + 200)
        result = _truncate(body, LESSON_ID)
        assert len(result) <= MAX_PROMPT_CHARS
        assert f"[see lesson {LESSON_ID} for full context]" in result

    def test_suffix_includes_lesson_id(self) -> None:
        body = "word " * 1000
        result = _truncate(body, "L21")
        assert "[see lesson L21 for full context]" in result


# ---------------------------------------------------------------------------
# GoogleAiStudioProvider
# ---------------------------------------------------------------------------


class TestGoogleAiStudioProvider:
    def setup_method(self) -> None:
        self.provider = GoogleAiStudioProvider()

    def test_build_url_contains_aistudio_domain(self) -> None:
        url = self.provider.build_url(SHORT_PROMPT, LESSON_ID)
        assert url is not None
        assert "aistudio.google.com" in url

    def test_build_url_contains_new_chat_path(self) -> None:
        url = self.provider.build_url(SHORT_PROMPT, LESSON_ID)
        assert url is not None
        assert "/prompts/new_chat" in url

    def test_build_url_contains_model_param(self) -> None:
        url = self.provider.build_url(SHORT_PROMPT, LESSON_ID)
        assert url is not None
        assert "model=gemini-2.5-flash" in url

    def test_build_url_contains_encoded_prompt(self) -> None:
        url = self.provider.build_url(SHORT_PROMPT, LESSON_ID)
        assert url is not None
        # Space in prompt must be encoded
        assert " " not in url.split("?", 1)[1]

    def test_build_url_short_prompt_not_truncated(self) -> None:
        url = self.provider.build_url(SHORT_PROMPT, LESSON_ID)
        assert url is not None
        assert "full+context" not in url  # truncation suffix absent

    def test_build_url_long_prompt_truncated(self) -> None:
        long_prompt = "This is a sentence. " * 300
        url = self.provider.build_url(long_prompt, LESSON_ID)
        assert url is not None
        assert "full+context" in url  # truncation suffix present (URL-encoded space → +)

    def test_build_url_returns_string(self) -> None:
        result = self.provider.build_url(SHORT_PROMPT, LESSON_ID)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# DisabledProvider
# ---------------------------------------------------------------------------


class TestDisabledProvider:
    def test_build_url_returns_none(self) -> None:
        provider = DisabledProvider()
        assert provider.build_url(SHORT_PROMPT, LESSON_ID) is None

    def test_build_url_returns_none_for_long_prompt(self) -> None:
        provider = DisabledProvider()
        assert provider.build_url("x" * 5000, LESSON_ID) is None


# ---------------------------------------------------------------------------
# get_handoff_provider factory
# ---------------------------------------------------------------------------


class TestGetHandoffProvider:
    def test_returns_google_ai_studio_by_default(self) -> None:
        with patch("handoff.settings") as mock_settings:
            mock_settings.handoff_provider = "google_ai_studio"
            provider = get_handoff_provider()
        assert isinstance(provider, GoogleAiStudioProvider)

    def test_returns_disabled_provider_when_configured(self) -> None:
        with patch("handoff.settings") as mock_settings:
            mock_settings.handoff_provider = "disabled"
            provider = get_handoff_provider()
        assert isinstance(provider, DisabledProvider)

    def test_case_insensitive_lookup(self) -> None:
        with patch("handoff.settings") as mock_settings:
            mock_settings.handoff_provider = "Google_AI_Studio"
            provider = get_handoff_provider()
        assert isinstance(provider, GoogleAiStudioProvider)

    def test_unknown_provider_falls_back_to_disabled(self) -> None:
        with patch("handoff.settings") as mock_settings:
            mock_settings.handoff_provider = "some_unknown_provider"
            provider = get_handoff_provider()
        assert isinstance(provider, DisabledProvider)

    def test_unknown_provider_logs_warning(self) -> None:
        with patch("handoff.settings") as mock_settings, \
             patch("handoff.logger") as mock_logger:
            mock_settings.handoff_provider = "nonexistent"
            get_handoff_provider()
        mock_logger.warning.assert_called_once()
