from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parent.parent


class ContentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Gemini generation
    gemini_model: str = "gemini-2.0-flash"
    generation_temperature: float = 0.7
    generation_max_output_tokens: int = 8192

    # Pipeline behaviour
    concurrency_limit: int = 5
    question_count: int = 8


settings = ContentSettings()
