from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Tuple, Type

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

    # Cloud SQL
    db_host: str = "127.0.0.1"
    db_port: int = 5432
    db_name: str = "learning_app"
    db_user: str = "app_user"
    db_password: str = ""
    db_connection_name: str = ""

    # App environment
    app_env: str = "development"

    # Model assignments (fixed per CLAUDE.md — do not change)
    context_agent_model: str = "gemini-2.5-flash-lite"
    lesson_agent_model: str = "gemini-2.5-flash"
    help_agent_model: str = "gemini-2.5-flash-lite"
    summary_agent_model: str = "gemini-2.5-flash-lite"

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
