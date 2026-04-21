"""
Integration test conftest — disables the unit-test autouse mock fixture.

The parent conftest.py patches all external I/O for unit tests. Integration
tests hit the real Cloud Run service and must NOT have those patches applied.
This local conftest overrides the autouse fixture with a no-op so the real
Gemini, Firestore, and GCS clients are used.
"""
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _patch_external_io(monkeypatch: pytest.MonkeyPatch) -> Any:  # type: ignore[override]
    """No-op override — integration tests use real external services."""
    yield
