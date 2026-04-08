"""
search_knowledge_base — ADK tool for pgvector cosine similarity search.

Called by LessonAgent to retrieve top-3 content chunks for a concept
at a given difficulty tier.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import psycopg2
import psycopg2.extras
import psycopg2.pool
import vertexai
from vertexai.language_models import TextEmbeddingInput, TextEmbeddingModel

from config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level singletons — initialized lazily on first call
# ---------------------------------------------------------------------------

_pool: psycopg2.pool.ThreadedConnectionPool | None = None
_embedding_model: TextEmbeddingModel | None = None


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        _pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=1,
            maxconn=5,
            host=settings.db_host,
            port=settings.db_port,
            dbname=settings.db_name,
            user=settings.db_user,
            password=settings.db_password,
            sslmode="disable",  # Cloud SQL proxy handles TLS; no second SSL layer needed
        )
        logger.info("DB connection pool initialized")
    return _pool


def _get_embedding_model() -> TextEmbeddingModel:
    global _embedding_model
    if _embedding_model is None:
        vertexai.init(project=settings.gcp_project_id, location=settings.gcp_location)
        _embedding_model = TextEmbeddingModel.from_pretrained("text-embedding-005")
        logger.info("Vertex AI embedding model loaded: text-embedding-005")
    return _embedding_model


# ---------------------------------------------------------------------------
# Sync helper functions — called via run_in_executor to avoid blocking
# ---------------------------------------------------------------------------


def _embed_query_sync(concept_id: str) -> list[float]:
    """Embed concept_id as a retrieval query vector (768-dim)."""
    model = _get_embedding_model()
    inputs = [TextEmbeddingInput(text=concept_id, task_type="RETRIEVAL_QUERY")]
    result = model.get_embeddings(inputs)
    return result[0].values


def _query_chunks_sync(vector: list[float], tier: str) -> list[dict[str, Any]]:
    """
    Run pgvector cosine similarity query, filtered by tier.

    IMPORTANT: tier filter is applied in WHERE clause BEFORE ORDER BY similarity
    to ensure the index is used correctly and results are tier-scoped.
    """
    pool = _get_pool()
    vector_str = "[" + ",".join(str(v) for v in vector) + "]"
    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    chunk_id::text,
                    lesson_id,
                    tier::text,
                    content_text,
                    1 - (embedding <=> %s::vector) AS similarity_score
                FROM content_chunks
                WHERE tier = %s::difficulty_tier
                ORDER BY embedding <=> %s::vector
                LIMIT 3
                """,
                (vector_str, tier, vector_str),
            )
            rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        pool.putconn(conn)


# ---------------------------------------------------------------------------
# Public async tool function — registered as an ADK tool (auto-wrapped)
# ---------------------------------------------------------------------------


async def search_knowledge_base(concept_id: str, tier: str) -> list[dict[str, Any]]:
    """
    Retrieve top-3 content chunks for a concept at a given difficulty tier.

    Args:
        concept_id: The concept identifier or query string to embed and search.
        tier: Difficulty tier — one of "beginner", "intermediate", "advanced".

    Returns:
        List of up to 3 dicts with keys: chunk_id, lesson_id, tier,
        content_text, similarity_score. Returns empty list on any error.
    """
    loop = asyncio.get_event_loop()
    try:
        vector = await loop.run_in_executor(None, _embed_query_sync, concept_id)
    except Exception:
        logger.exception("Vertex AI embedding failed for concept_id=%s", concept_id)
        return []
    try:
        chunks = await loop.run_in_executor(None, _query_chunks_sync, vector, tier)
    except Exception:
        logger.exception("pgvector query failed for concept_id=%s tier=%s", concept_id, tier)
        return []
    logger.info(
        "search_knowledge_base: %d chunks returned",
        len(chunks),
        extra={"concept_id": concept_id, "tier": tier},
    )
    return chunks
