"""
Unit tests for the three response-handling helpers in generate_content.py:

  _extract_text      — raises ValueError when response.text is None
  _strip_code_fence  — strips markdown ```json ... ``` fences
  _call_with_retry   — retries on transient errors / None responses with backoff
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import google.api_core.exceptions  # ty: ignore[unresolved-import]
import pytest

# ---------------------------------------------------------------------------
# Import the helpers directly from the pipeline module.
# The module imports google.auth at load time; patch it before importing.
# ---------------------------------------------------------------------------
import sys
import types

# Stub out heavy third-party imports so the module can be imported without
# real credentials or the full google-genai SDK installed in test environments.
_STUBS = {
    "google.auth": MagicMock(),
    "google.auth.transport": MagicMock(),
    "google.auth.transport.requests": MagicMock(),
    "google.genai": MagicMock(),
    "google.genai.types": MagicMock(),
    "yaml": MagicMock(),
}
for mod_name, stub in _STUBS.items():
    sys.modules.setdefault(mod_name, stub)

# config and review_models are local; stub them too so we don't need .env
_config_stub: Any = types.ModuleType("config")
_settings = MagicMock()
_settings.gemini_model = "gemini-test"
_settings.reviewer_model = "gemini-reviewer-test"
_settings.generation_max_output_tokens = 8192
_settings.reviewer_max_output_tokens = 4096
_settings.concurrency_limit = 5
_config_stub.settings = _settings  # ty: ignore[unresolved-attribute]
sys.modules.setdefault("config", _config_stub)

_review_stub: Any = types.ModuleType("review_models")
_review_stub.ReviewResult = MagicMock()  # ty: ignore[unresolved-attribute]
sys.modules.setdefault("review_models", _review_stub)

_token_stub: Any = types.ModuleType("token_usage_log")
_token_stub.PipelineLogger = MagicMock()  # ty: ignore[unresolved-attribute]
sys.modules.setdefault("token_usage_log", _token_stub)

# Now import the helpers
from generate_content import (  # noqa: E402
    _MAX_RETRIES,
    _RETRY_BASE_DELAY,
    _call_with_retry,
    _extract_text,
    _strip_code_fence,
)


# ===========================================================================
# _extract_text
# ===========================================================================


class TestExtractText:
    def _response(self, text: str | None, finish_reason: Any = None) -> Any:
        candidate = SimpleNamespace(finish_reason=finish_reason)
        return SimpleNamespace(
            text=text,
            candidates=[candidate] if finish_reason is not None else [],
        )

    def test_returns_text_when_present(self) -> None:
        r = self._response('{"lesson": {}}')
        assert _extract_text(r, "[L01 Beginner]") == '{"lesson": {}}'

    def test_raises_when_text_is_none_no_candidates(self) -> None:
        r = SimpleNamespace(text=None, candidates=[])
        with pytest.raises(ValueError, match="finish_reason=unknown"):
            _extract_text(r, "[L01 Beginner]")

    def test_raises_when_text_is_none_with_finish_reason(self) -> None:
        r = self._response(None, finish_reason="MAX_TOKENS")
        with pytest.raises(ValueError, match="finish_reason=MAX_TOKENS"):
            _extract_text(r, "[L01 Beginner]")

    def test_raises_when_text_is_none_safety(self) -> None:
        r = self._response(None, finish_reason="SAFETY")
        with pytest.raises(ValueError, match="finish_reason=SAFETY"):
            _extract_text(r, "[L02 Advanced]")

    def test_raises_when_candidates_attr_missing(self) -> None:
        """response objects without a candidates attribute should not crash."""
        r = SimpleNamespace(text=None)
        with pytest.raises(ValueError, match="finish_reason=unknown"):
            _extract_text(r, "[L03 Intermediate]")


# ===========================================================================
# _strip_code_fence
# ===========================================================================


class TestStripCodeFence:
    def test_plain_json_unchanged(self) -> None:
        raw = '{"lesson": "hello"}'
        assert _strip_code_fence(raw) == raw

    def test_strips_json_fence(self) -> None:
        raw = '```json\n{"lesson": "hello"}\n```'
        assert _strip_code_fence(raw) == '{"lesson": "hello"}'

    def test_strips_bare_fence(self) -> None:
        raw = "```\n{}\n```"
        assert _strip_code_fence(raw) == "{}"

    def test_strips_leading_trailing_whitespace(self) -> None:
        raw = "  \n```json\n{}\n```\n  "
        assert _strip_code_fence(raw) == "{}"

    def test_no_closing_fence_left_intact(self) -> None:
        """Partial fence (no closing ```) should still strip the opening."""
        raw = "```json\n{}"
        result = _strip_code_fence(raw)
        # Opening fence stripped; no closing fence to remove
        assert result == "{}"

    def test_empty_string(self) -> None:
        assert _strip_code_fence("") == ""

    def test_fence_with_no_language_tag(self) -> None:
        raw = "```\n[1, 2, 3]\n```"
        assert _strip_code_fence(raw) == "[1, 2, 3]"


# ===========================================================================
# _call_with_retry
# ===========================================================================


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch asyncio.sleep so tests don't actually wait."""
    monkeypatch.setattr("generate_content.asyncio.sleep", AsyncMock())


def _good_response(text: str = '{"lesson": {}, "quiz": []}') -> Any:
    return SimpleNamespace(text=text)


def _none_response() -> Any:
    return SimpleNamespace(text=None, candidates=[])


class TestCallWithRetry:
    async def test_success_on_first_attempt(self) -> None:
        fn = MagicMock(return_value=_good_response())
        result = await _call_with_retry(fn, "[L01 Beginner]")
        assert result.text == '{"lesson": {}, "quiz": []}'
        assert fn.call_count == 1

    async def test_retries_on_resource_exhausted_then_succeeds(self) -> None:
        exc = google.api_core.exceptions.ResourceExhausted("quota")
        fn = MagicMock(side_effect=[exc, _good_response()])
        result = await _call_with_retry(fn, "[L01 Beginner]")
        assert result.text is not None
        assert fn.call_count == 2

    async def test_retries_on_service_unavailable_then_succeeds(self) -> None:
        exc = google.api_core.exceptions.ServiceUnavailable("503")
        fn = MagicMock(side_effect=[exc, exc, _good_response()])
        result = await _call_with_retry(fn, "[L01 Beginner]")
        assert fn.call_count == 3

    async def test_raises_after_max_retries_on_resource_exhausted(self) -> None:
        exc = google.api_core.exceptions.ResourceExhausted("quota")
        fn = MagicMock(side_effect=exc)
        with pytest.raises(google.api_core.exceptions.ResourceExhausted):
            await _call_with_retry(fn, "[L01 Beginner]")
        assert fn.call_count == _MAX_RETRIES

    async def test_raises_after_max_retries_on_deadline_exceeded(self) -> None:
        exc = google.api_core.exceptions.DeadlineExceeded("timeout")
        fn = MagicMock(side_effect=exc)
        with pytest.raises(google.api_core.exceptions.DeadlineExceeded):
            await _call_with_retry(fn, "[L01 Beginner]")
        assert fn.call_count == _MAX_RETRIES

    async def test_retries_on_none_response_then_succeeds(self) -> None:
        fn = MagicMock(side_effect=[_none_response(), _good_response()])
        result = await _call_with_retry(fn, "[L01 Beginner]")
        assert result.text is not None
        assert fn.call_count == 2

    async def test_raises_value_error_when_none_exhausts_retries(self) -> None:
        fn = MagicMock(return_value=_none_response())
        with pytest.raises(ValueError, match="response.text remained None"):
            await _call_with_retry(fn, "[L01 Beginner]")
        assert fn.call_count == _MAX_RETRIES

    async def test_does_not_retry_invalid_argument(self) -> None:
        """InvalidArgument is a non-retryable error — should propagate immediately."""
        exc = google.api_core.exceptions.InvalidArgument("bad prompt")
        fn = MagicMock(side_effect=exc)
        with pytest.raises(google.api_core.exceptions.InvalidArgument):
            await _call_with_retry(fn, "[L01 Beginner]")
        assert fn.call_count == 1

    async def test_backoff_delay_increases_exponentially(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Each retry should wait _RETRY_BASE_DELAY * 2^(attempt-1) seconds."""
        sleep_calls: list[float] = []

        async def capture_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        monkeypatch.setattr("generate_content.asyncio.sleep", capture_sleep)

        exc = google.api_core.exceptions.ResourceExhausted("quota")
        fn = MagicMock(side_effect=[exc, exc, _good_response()])
        await _call_with_retry(fn, "[L01 Beginner]")

        assert sleep_calls == [
            _RETRY_BASE_DELAY * (2**0),  # attempt 1 → 2s
            _RETRY_BASE_DELAY * (2**1),  # attempt 2 → 4s
        ]

    async def test_none_response_backoff_delay(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """None-response retries should also use exponential backoff."""
        sleep_calls: list[float] = []

        async def capture_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        monkeypatch.setattr("generate_content.asyncio.sleep", capture_sleep)

        fn = MagicMock(side_effect=[_none_response(), _good_response()])
        await _call_with_retry(fn, "[L01 Beginner]")

        assert sleep_calls == [_RETRY_BASE_DELAY * (2**0)]  # attempt 1 → 2s
