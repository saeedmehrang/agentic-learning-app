from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Tuple, Type

from pydantic import field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # GCP
    gcp_project_id: str = "agentic-learning-app-e13cb"
    gcp_location: str = "us-central1"

    # Content storage — GCS bucket for approved lesson files, outlines, and concept map.
    # Set GCS_PIPELINE_BUCKET to the bucket name to read from GCS (production).
    # Leave empty to fall back to local filesystem under courses/linux-basics/ (dev/tests).
    gcs_pipeline_bucket: str = ""

    @field_validator("gcp_project_id", mode="before")
    @classmethod
    def _default_project_id(cls, v: str) -> str:
        # Devcontainer sets GCP_PROJECT_ID="" in env; fall back to hardcoded default.
        return v or "agentic-learning-app-e13cb"

    @field_validator("gcp_location", mode="before")
    @classmethod
    def _default_location(cls, v: str) -> str:
        return v or "us-central1"

    # App environment
    app_env: str = "development"
    # Deployment version — set to $COMMIT_SHA at deploy time (gcloud run deploy --set-env-vars).
    # Defaults to "dev" for local runs. Used to group metrics by deployment in Cloud Monitoring.
    app_version: str = "dev"

    # Rate limiting — session starts per UID per rolling 60-minute window.
    # Override with MAX_SESSIONS_PER_HOUR env var at deploy time.
    max_sessions_per_hour: int = 10

    # Model assignments (fixed per spec — do not change without updating spec)
    lesson_model: str = "gemini-3.1-flash-lite-preview"
    help_model: str = "gemini-3.1-flash-lite-preview"
    summary_model: str = "gemini-3.1-flash-lite-preview"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: Type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> Tuple[PydanticBaseSettingsSource, ...]:
        # Pull secrets from Secret Manager via ADC — no plaintext secrets on disk.
        # Falls back gracefully to env-only if Secret Manager is unreachable (e.g.
        # unit tests without ADC). env_settings still allows non-secret config overrides.
        from pydantic_settings import GoogleSecretManagerSettingsSource

        class _SafeSecretManagerSource(GoogleSecretManagerSettingsSource):
            """Wraps GoogleSecretManagerSettingsSource to suppress errors in envs
            where ADC is unavailable (CI, unit tests). Secrets fall through to env vars."""

            def __call__(self) -> dict[str, Any]:  # type: ignore[override]
                try:
                    return super().__call__()
                except Exception:
                    return {}

        return (
            init_settings,
            env_settings,
            _SafeSecretManagerSource(
                settings_cls,
                project_id=os.environ.get("GCP_PROJECT_ID") or "agentic-learning-app-e13cb",
                case_sensitive=False,
            ),
        )


settings = Settings()
