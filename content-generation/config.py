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
    gemini_model: str = "gemini-3.1-flash-lite-preview"
    generation_temperature: float = 0.7
    generation_max_output_tokens: int = 8192
    generation_thinking_level: str | None = "medium"  # Gemini 3 series only: minimal | low | medium | high

    # Reviewer LLM
    reviewer_model: str = "gemini-3-flash-preview"
    reviewer_temperature: float = 0.2
    reviewer_max_output_tokens: int = 8192
    reviewer_thinking_level: str | None = "medium"  # Gemini 3 series only: minimal | low | medium | high

    # Pipeline behaviour
    concurrency_limit: int = 5
    question_count: int = 8

    # Vertex AI (embeddings)
    gcp_project_id: str = "agentic-learning-app-e13cb"
    gcp_location: str = "us-central1"
    embedding_model: str = "text-embedding-005"
    embedding_concurrency_limit: int = 5

    # Storage backend — set GCS_PIPELINE_BUCKET to route all pipeline I/O through GCS.
    # Leave empty (default) to use the local filesystem under courses/linux-basics/pipeline/.
    gcs_pipeline_bucket: str = ""


settings = ContentSettings()
