from __future__ import annotations

import os
from pathlib import Path
from typing import Tuple, Type

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
    context_agent_model: str = "gemini-2.5-flash-preview-05-20"
    lesson_agent_model: str = "gemini-2.5-flash-preview-05-20"
    help_agent_model: str = "gemini-2.5-flash-lite-preview-06-17"
    summary_agent_model: str = "gemini-2.5-flash-lite-preview-06-17"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: Type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> Tuple[PydanticBaseSettingsSource, ...]:
        if os.environ.get("APP_ENV") == "production":
            # Import here to avoid requiring google-cloud-secret-manager in dev
            from pydantic_settings import GoogleSecretManagerSettingsSource

            return (
                init_settings,
                env_settings,
                GoogleSecretManagerSettingsSource(
                    settings_cls,
                    project_id=os.environ.get("GCP_PROJECT_ID", "agentic-learning-app-e13cb"),
                ),
            )
        return (init_settings, env_settings, dotenv_settings)


settings = Settings()
