"""
token_usage_log.py — Thread-safe CSV logger for Gemini API token usage.

Each API call appends one row to courses/linux-basics/pipeline/token_usage.csv.
Token counts come directly from the Gemini API response (response.usage_metadata),
so they are exact — not estimates.

CSV columns
-----------
timestamp_utc           ISO-8601 timestamp of the API call (UTC).
call_type               One of: generate | review | regenerate.
lesson_id               Lesson identifier, e.g. "L01".
tier                    Difficulty tier: Beginner | Intermediate | Advanced.
model                   Gemini model name used for this call.
max_output_tokens_config  The max_output_tokens value set in config for this call type.
                          Useful for spotting calls where the output was likely truncated
                          (candidates_tokens ≈ max_output_tokens_config).
prompt_tokens           Tokens consumed by the input prompt (including any cached content).
candidates_tokens       Tokens in the model's output (the generated JSON).
thoughts_tokens         Tokens used by the model's internal reasoning (Gemini 3.x thinking
                        mode only; 0 for models that do not support thinking).
total_tokens            Sum of prompt + candidates + thoughts tokens for the full call.

Importable API
--------------
    from token_usage_log import TokenUsageLogger

    usage_logger = TokenUsageLogger()             # uses default path
    usage_logger.record(
        call_type="generate",
        lesson_id="L01",
        tier="Beginner",
        model=settings.gemini_model,
        max_output_tokens=settings.generation_max_output_tokens,
        usage_metadata=response.usage_metadata,
    )
    usage_logger.print_session_summary()
"""

from __future__ import annotations

import csv
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import google.genai.types as genai_types

logger = logging.getLogger(__name__)

_CSV_PATH = (
    Path(__file__).resolve().parent.parent
    / "courses" / "linux-basics" / "pipeline" / "token_usage.csv"
)

_CSV_FIELDNAMES = [
    "timestamp_utc",
    "call_type",       # generate | review | regenerate
    "lesson_id",
    "tier",
    "model",
    "max_output_tokens_config",
    "prompt_tokens",
    "candidates_tokens",
    "thoughts_tokens",
    "total_tokens",
]


@dataclass
class _UsageRow:
    timestamp_utc: str
    call_type: str
    lesson_id: str
    tier: str
    model: str
    max_output_tokens_config: int
    prompt_tokens: int
    candidates_tokens: int
    thoughts_tokens: int
    total_tokens: int

    def as_dict(self) -> dict[str, object]:
        return {
            "timestamp_utc": self.timestamp_utc,
            "call_type": self.call_type,
            "lesson_id": self.lesson_id,
            "tier": self.tier,
            "model": self.model,
            "max_output_tokens_config": self.max_output_tokens_config,
            "prompt_tokens": self.prompt_tokens,
            "candidates_tokens": self.candidates_tokens,
            "thoughts_tokens": self.thoughts_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass
class TokenUsageLogger:
    """Accumulates per-call token usage and writes rows to a CSV file.

    Thread-safe: a single lock serialises CSV writes across async executor
    threads.
    """

    csv_path: Path = field(default_factory=lambda: _CSV_PATH)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _rows: list[_UsageRow] = field(default_factory=list, init=False, repr=False)

    def record(
        self,
        *,
        call_type: str,
        lesson_id: str,
        tier: str,
        model: str,
        max_output_tokens: int,
        usage_metadata: Optional[genai_types.GenerateContentResponseUsageMetadata],
    ) -> None:
        """Extract token counts from usage_metadata and append to CSV + session totals."""
        if usage_metadata is None:
            logger.debug(
                "[token_usage] No usage_metadata on response for %s %s (%s) — skipping.",
                lesson_id, tier, call_type,
            )
            return

        prompt_tokens = usage_metadata.prompt_token_count or 0
        candidates_tokens = usage_metadata.candidates_token_count or 0
        thoughts_tokens = usage_metadata.thoughts_token_count or 0
        total_tokens = usage_metadata.total_token_count or (
            prompt_tokens + candidates_tokens + thoughts_tokens
        )

        row = _UsageRow(
            timestamp_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            call_type=call_type,
            lesson_id=lesson_id,
            tier=tier,
            model=model,
            max_output_tokens_config=max_output_tokens,
            prompt_tokens=prompt_tokens,
            candidates_tokens=candidates_tokens,
            thoughts_tokens=thoughts_tokens,
            total_tokens=total_tokens,
        )

        logger.debug(
            "[token_usage] %s %s (%s) — prompt=%d, candidates=%d, thoughts=%d, total=%d",
            lesson_id, tier, call_type,
            prompt_tokens, candidates_tokens, thoughts_tokens, total_tokens,
        )

        with self._lock:
            self._rows.append(row)
            self._append_csv(row)

    def _append_csv(self, row: _UsageRow) -> None:
        """Write one row to CSV, creating the file with a header if it does not exist."""
        write_header = not self.csv_path.exists()
        try:
            with self.csv_path.open("a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=_CSV_FIELDNAMES)
                if write_header:
                    writer.writeheader()
                writer.writerow(row.as_dict())
        except OSError as exc:
            logger.warning("[token_usage] Could not write CSV row: %s", exc)

    def session_totals(self) -> dict[str, int]:
        """Return summed token counts for all calls recorded this session."""
        totals: dict[str, int] = {
            "prompt_tokens": 0,
            "candidates_tokens": 0,
            "thoughts_tokens": 0,
            "total_tokens": 0,
        }
        for row in self._rows:
            totals["prompt_tokens"] += row.prompt_tokens
            totals["candidates_tokens"] += row.candidates_tokens
            totals["thoughts_tokens"] += row.thoughts_tokens
            totals["total_tokens"] += row.total_tokens
        return totals

    def print_session_summary(self) -> None:
        """Print a formatted token usage summary for this pipeline run."""
        if not self._rows:
            return

        totals = self.session_totals()
        calls_by_type: dict[str, int] = {}
        for row in self._rows:
            calls_by_type[row.call_type] = calls_by_type.get(row.call_type, 0) + 1

        call_summary = ", ".join(
            f"{count} {call_type}" for call_type, count in sorted(calls_by_type.items())
        )
        logger.info(
            "\nToken usage this session (%s API calls: %s)\n"
            "  %-20s %8d\n"
            "  %-20s %8d\n"
            "  %-20s %8d\n"
            "  %-20s %8d\n"
            "  Logged to: %s",
            len(self._rows), call_summary,
            "prompt tokens:", totals["prompt_tokens"],
            "candidates tokens:", totals["candidates_tokens"],
            "thoughts tokens:", totals["thoughts_tokens"],
            "total tokens:", totals["total_tokens"],
            self.csv_path,
        )
