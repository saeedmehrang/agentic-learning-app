"""
Unit tests for logging_config — JsonFormatter and configure_logging.

All tests are synchronous and in-process. No I/O.
"""
from __future__ import annotations

import json
import logging

import pytest

from logging_config import JsonFormatter, configure_logging

# ---------------------------------------------------------------------------
# JsonFormatter
# ---------------------------------------------------------------------------


class TestJsonFormatter:
    """Tests for the JSON log record formatter used in Cloud Run."""

    @pytest.fixture()
    def formatter(self) -> JsonFormatter:
        return JsonFormatter()

    def _make_record(
        self,
        message: str = "hello",
        level: int = logging.INFO,
        name: str = "test_logger",
        **extra: object,
    ) -> logging.LogRecord:
        record = logging.LogRecord(
            name=name,
            level=level,
            pathname=__file__,
            lineno=0,
            msg=message,
            args=(),
            exc_info=None,
        )
        for k, v in extra.items():
            setattr(record, k, v)
        return record

    def test_output_is_valid_json(self, formatter: JsonFormatter) -> None:
        record = self._make_record("test message")
        output = formatter.format(record)
        parsed = json.loads(output)  # raises if invalid JSON
        assert isinstance(parsed, dict)

    def test_required_keys_present(self, formatter: JsonFormatter) -> None:
        record = self._make_record("some message")
        parsed = json.loads(formatter.format(record))
        for key in ("timestamp", "severity", "message", "logger"):
            assert key in parsed, f"Missing required key: {key}"

    def test_severity_matches_log_level(self, formatter: JsonFormatter) -> None:
        for level, name in (
            (logging.DEBUG, "DEBUG"),
            (logging.INFO, "INFO"),
            (logging.WARNING, "WARNING"),
            (logging.ERROR, "ERROR"),
            (logging.CRITICAL, "CRITICAL"),
        ):
            record = self._make_record("msg", level=level)
            parsed = json.loads(formatter.format(record))
            assert parsed["severity"] == name

    def test_message_field_matches_log_message(self, formatter: JsonFormatter) -> None:
        record = self._make_record("expected content")
        parsed = json.loads(formatter.format(record))
        assert parsed["message"] == "expected content"

    def test_logger_field_matches_logger_name(self, formatter: JsonFormatter) -> None:
        record = self._make_record(name="my.module")
        parsed = json.loads(formatter.format(record))
        assert parsed["logger"] == "my.module"

    def test_timestamp_is_iso8601(self, formatter: JsonFormatter) -> None:
        """timestamp must be parseable as ISO 8601 and be non-empty."""
        from datetime import datetime
        record = self._make_record()
        parsed = json.loads(formatter.format(record))
        ts = parsed["timestamp"]
        dt = datetime.fromisoformat(ts)  # raises if not valid ISO 8601
        assert dt is not None

    def test_extra_fields_are_included(self, formatter: JsonFormatter) -> None:
        """Fields added via logger.info(..., extra={}) must appear in output."""
        record = self._make_record("msg", session_id="abc-123", uid="user-42")
        parsed = json.loads(formatter.format(record))
        assert parsed["session_id"] == "abc-123"
        assert parsed["uid"] == "user-42"

    def test_internal_logging_keys_are_not_leaked(self, formatter: JsonFormatter) -> None:
        """Python-internal log record attributes must not appear in JSON output."""
        record = self._make_record("msg")
        parsed = json.loads(formatter.format(record))
        for key in ("msg", "args", "levelno", "pathname", "filename", "lineno",
                    "funcName", "thread", "process"):
            assert key not in parsed, f"Internal key leaked into output: {key}"

    def test_exception_info_serialised_as_string(self, formatter: JsonFormatter) -> None:
        """When exc_info is present the 'exception' key must appear as a non-empty string."""
        try:
            raise ValueError("boom")
        except ValueError:
            import sys
            exc_info = sys.exc_info()
        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=0,
            msg="error occurred",
            args=(),
            exc_info=exc_info,
        )
        parsed = json.loads(formatter.format(record))
        assert "exception" in parsed
        assert "ValueError" in parsed["exception"]

    def test_non_serialisable_extra_does_not_raise(self, formatter: JsonFormatter) -> None:
        """Non-JSON-serialisable extras (e.g. a custom object) must not crash the formatter."""
        class _Opaque:
            def __repr__(self) -> str:
                return "<opaque>"

        record = self._make_record("msg", opaque=_Opaque())
        output = formatter.format(record)  # must not raise
        parsed = json.loads(output)
        assert "opaque" in parsed


# ---------------------------------------------------------------------------
# configure_logging
# ---------------------------------------------------------------------------


class TestConfigureLogging:
    def test_root_logger_has_json_formatter_after_configure(self) -> None:
        configure_logging()
        root = logging.getLogger()
        assert len(root.handlers) >= 1
        handler = root.handlers[0]
        assert isinstance(handler.formatter, JsonFormatter)

    def test_calling_twice_does_not_duplicate_handlers(self) -> None:
        configure_logging()
        configure_logging()
        root = logging.getLogger()
        assert len(root.handlers) == 1

    def test_log_level_is_applied(self) -> None:
        configure_logging(level=logging.WARNING)
        root = logging.getLogger()
        assert root.level == logging.WARNING
        # Restore for other tests
        configure_logging(level=logging.INFO)

    def test_logger_produces_valid_json_output(self, capfd: pytest.CaptureFixture[str]) -> None:
        """End-to-end: a real log call must produce parseable JSON on stdout."""
        configure_logging()
        logger = logging.getLogger("json_output_test")
        logger.info("integration check", extra={"check_key": "check_value"})
        captured = capfd.readouterr()
        # There may be multiple lines; find the one that contains our message
        for line in captured.out.splitlines():
            if not line.strip():
                continue
            parsed = json.loads(line)
            if parsed.get("message") == "integration check":
                assert parsed["check_key"] == "check_value"
                assert parsed["severity"] == "INFO"
                break
        else:
            pytest.fail("Expected log line not found in stdout")
