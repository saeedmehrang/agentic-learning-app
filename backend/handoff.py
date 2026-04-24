"""
Pluggable handoff provider — builds a deep-link URL to an external AI chat
surface so learners can continue after 3 unresolved HelpSession turns.

Adding a new provider:
    1. Implement a class with `build_url(prompt, lesson_id) -> str | None`
    2. Register it in _PROVIDERS with a string key
    3. Set HANDOFF_PROVIDER=<key> in the Cloud Run environment

The rest of the system (main.py, Flutter) is unaffected.
"""
from __future__ import annotations

import logging
from typing import Protocol
from urllib.parse import urlencode

from config import settings

logger = logging.getLogger(__name__)

MAX_PROMPT_CHARS = 3000


class HandoffProvider(Protocol):
    def build_url(self, prompt: str, lesson_id: str) -> str | None:
        ...


class GoogleAiStudioProvider:
    _BASE = "https://aistudio.google.com/prompts/new_chat"
    _MODEL = "gemini-2.5-flash"

    def build_url(self, prompt: str, lesson_id: str) -> str | None:
        truncated = _truncate(prompt, lesson_id)
        params = urlencode({"prompt": truncated, "model": self._MODEL})
        return f"{self._BASE}?{params}"


class DisabledProvider:
    def build_url(self, prompt: str, lesson_id: str) -> str | None:
        return None


def _truncate(prompt: str, lesson_id: str) -> str:
    """Truncate prompt to MAX_PROMPT_CHARS at the last sentence boundary."""
    if len(prompt) <= MAX_PROMPT_CHARS:
        return prompt
    suffix = f" [see lesson {lesson_id} for full context]"
    cutoff = MAX_PROMPT_CHARS - len(suffix)
    candidate = prompt[:cutoff]
    last_boundary = max(candidate.rfind("."), candidate.rfind("?"), candidate.rfind("!"))
    if last_boundary > 0:
        candidate = candidate[: last_boundary + 1]
    return candidate + suffix


_PROVIDERS: dict[str, HandoffProvider] = {
    "google_ai_studio": GoogleAiStudioProvider(),
    "disabled": DisabledProvider(),
}


def get_handoff_provider() -> HandoffProvider:
    key = settings.handoff_provider.lower()
    provider = _PROVIDERS.get(key)
    if provider is None:
        logger.warning("Unknown HANDOFF_PROVIDER=%r — falling back to disabled", key)
        return DisabledProvider()
    return provider
