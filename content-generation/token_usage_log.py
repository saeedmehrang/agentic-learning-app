"""
token_usage_log.py — Async-safe JSON logger for pipeline progress and Gemini token usage.

Writes to courses/linux-basics/pipeline/pipeline_log.json with two top-level sections:

  "progress"    — one record per lesson × tier combination, updated in-place as the
                  combination moves through the pipeline phases.
  "token_usage" — append-only array of per-API-call token counts.

Token counts come directly from the Gemini API response (response.usage_metadata),
so they are exact — not estimates.

Progress statuses
-----------------
generating      Phase 1 API call in flight
generated       Phase 1 complete; content written to pipeline/generated/
reviewing       Phase 2 review call in flight
reviewed        Phase 2 complete; review written to pipeline/reviewed/
regenerating    Phase 3 regen call in flight
approved        Pipeline complete; content written to pipeline/approved/
failed          Unrecoverable error at any phase
skipped         Combination skipped (--resume, already approved)

Importable API
--------------
    from token_usage_log import PipelineLogger

    pipeline_logger = PipelineLogger()

    pipeline_logger.record_token_usage(
        call_type="generate",
        lesson_id="L01",
        tier="Beginner",
        model=settings.gemini_model,
        usage_metadata=response.usage_metadata,
    )

    await pipeline_logger.update_progress("L01", "Beginner", "approved",
                                          regenerated=False, blocking_issues=0)

    pipeline_logger.print_session_summary()
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import google.genai.types as genai_types

logger = logging.getLogger(__name__)

_LOG_PATH = (
    Path(__file__).resolve().parent.parent
    / "courses" / "linux-basics" / "pipeline" / "pipeline_log.json"
)

# Statuses that record a terminal timestamp into the progress entry.
_TIMESTAMP_FIELD: dict[str, str] = {
    "generated": "generated_at",
    "reviewed": "reviewed_at",
    "approved": "approved_at",
    "failed": "failed_at",
}


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _empty_log() -> dict[str, Any]:
    return {"progress": {}, "token_usage": []}


def _read_log(path: Path) -> dict[str, Any]:
    """Read the log file, returning an empty structure if missing or corrupt."""
    if not path.exists():
        return _empty_log()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if "progress" not in data or "token_usage" not in data:
            return _empty_log()
        return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("[pipeline_log] Could not read log file: %s — starting fresh.", exc)
        return _empty_log()


def _write_log(path: Path, data: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        logger.warning("[pipeline_log] Could not write log file: %s", exc)


@dataclass
class _TokenUsageRow:
    timestamp_utc: str
    call_type: str
    lesson_id: str
    tier: str
    model: str
    prompt_tokens: int
    candidates_tokens: int
    thoughts_tokens: int
    total_tokens: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "timestamp_utc": self.timestamp_utc,
            "call_type": self.call_type,
            "lesson_id": self.lesson_id,
            "tier": self.tier,
            "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "candidates_tokens": self.candidates_tokens,
            "thoughts_tokens": self.thoughts_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass
class PipelineLogger:
    """Tracks per-combination pipeline progress and per-call token usage.

    Async-safe: a single asyncio.Lock serialises all reads and writes to
    pipeline_log.json, which is safe for the concurrent asyncio tasks in
    generate_content.py.
    """

    log_path: Path = field(default_factory=lambda: _LOG_PATH)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _rows: list[_TokenUsageRow] = field(default_factory=list, init=False, repr=False)

    # ------------------------------------------------------------------
    # Token usage
    # ------------------------------------------------------------------

    def record_token_usage(
        self,
        *,
        call_type: str,
        lesson_id: str,
        tier: str,
        model: str,
        usage_metadata: Optional[genai_types.GenerateContentResponseUsageMetadata],
    ) -> None:
        """Extract token counts from usage_metadata and schedule a log write.

        This method is synchronous so callers do not need to await it. The
        log write is dispatched via asyncio.ensure_future and protected by
        the asyncio.Lock.
        """
        if usage_metadata is None:
            logger.debug(
                "[pipeline_log] No usage_metadata for %s %s (%s) — skipping.",
                lesson_id, tier, call_type,
            )
            return

        prompt_tokens = usage_metadata.prompt_token_count or 0
        candidates_tokens = usage_metadata.candidates_token_count or 0
        thoughts_tokens = usage_metadata.thoughts_token_count or 0
        total_tokens = usage_metadata.total_token_count or (
            prompt_tokens + candidates_tokens + thoughts_tokens
        )

        row = _TokenUsageRow(
            timestamp_utc=_now_utc(),
            call_type=call_type,
            lesson_id=lesson_id,
            tier=tier,
            model=model,
            prompt_tokens=prompt_tokens,
            candidates_tokens=candidates_tokens,
            thoughts_tokens=thoughts_tokens,
            total_tokens=total_tokens,
        )

        logger.debug(
            "[pipeline_log] %s %s (%s) — prompt=%d, candidates=%d, thoughts=%d, total=%d",
            lesson_id, tier, call_type,
            prompt_tokens, candidates_tokens, thoughts_tokens, total_tokens,
        )

        self._rows.append(row)
        asyncio.ensure_future(self._append_token_usage(row))

    async def _append_token_usage(self, row: _TokenUsageRow) -> None:
        async with self._lock:
            data = _read_log(self.log_path)
            data["token_usage"].append(row.as_dict())
            _write_log(self.log_path, data)

    # ------------------------------------------------------------------
    # Progress tracking
    # ------------------------------------------------------------------

    async def update_progress(
        self,
        lesson_id: str,
        tier: str,
        status: str,
        *,
        error: Optional[str] = None,
        regenerated: Optional[bool] = None,
        blocking_issues: Optional[int] = None,
    ) -> None:
        """Upsert a progress record for the given lesson × tier combination.

        Preserves existing timestamp fields — only adds new ones appropriate
        for the given status.
        """
        key = f"{lesson_id}_{tier.lower()}"

        async with self._lock:
            data = _read_log(self.log_path)
            entry: dict[str, Any] = data["progress"].get(key, {})

            entry["status"] = status

            ts_field = _TIMESTAMP_FIELD.get(status)
            if ts_field:
                entry[ts_field] = _now_utc()

            if error is not None:
                entry["error"] = error
            if regenerated is not None:
                entry["regenerated"] = regenerated
            if blocking_issues is not None:
                entry["blocking_issues"] = blocking_issues

            data["progress"][key] = entry
            _write_log(self.log_path, data)

    # ------------------------------------------------------------------
    # Session summary
    # ------------------------------------------------------------------

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
            self.log_path,
        )
