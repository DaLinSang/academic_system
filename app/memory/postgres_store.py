"""PostgreSQL-backed long-term memory storage with vector similarity search."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import httpx
import psycopg
from pgvector.psycopg import register_vector

from app.core.config import settings
from app.core.errors import MemoryStoreError
from app.core.types import MemoryCandidate, MemoryItem, MemoryTier


class Embedder(Protocol):
    """Create vectors for memory content and search queries."""

    def embed(self, text: str) -> list[float]:
        ...


class OllamaMemoryEmbedder:
    """Generate memory embeddings using the project's configured Ollama model."""

    def __init__(self, base_url: str | None = None, model: str | None = None) -> None:
        self.base_url = (base_url or settings.embedding.ollama_base_url).rstrip("/")
        self.model = model or settings.embedding.model

    def embed(self, text: str) -> list[float]:
        try:
            response = httpx.post(
                f"{self.base_url}/api/embeddings",
                json={"model": self.model, "prompt": text},
                timeout=30.0,
                trust_env=False,
            )
            response.raise_for_status()
            vector = response.json().get("embedding")
            if not isinstance(vector, list) or len(vector) != settings.embedding.dim:
                raise ValueError(
                    f"Expected {settings.embedding.dim}-dimensional embedding"
                )
            return [float(value) for value in vector]
        except Exception as exc:
            raise MemoryStoreError(f"Memory embedding failed: {exc}") from exc


class PostgresMemoryStore:
    """Persist user memories and rank them by semantic relevance, recency and importance."""

    def __init__(
        self,
        dsn: str | None = None,
        embedder: Embedder | None = None,
        relevance_weight: float = 0.6,
        recency_weight: float = 0.2,
        importance_weight: float = 0.2,
        min_similarity: float = 0.3,
    ) -> None:
        if min(relevance_weight, recency_weight, importance_weight) < 0:
            raise ValueError("Memory search weights must be non-negative")
        total_weight = relevance_weight + recency_weight + importance_weight
        if total_weight == 0:
            raise ValueError("At least one memory search weight must be positive")
        if not -1.0 <= min_similarity <= 1.0:
            raise ValueError("min_similarity must be between -1 and 1")

        self.dsn = dsn or settings.db.dsn
        self.embedder = embedder or OllamaMemoryEmbedder()
        self.relevance_weight = relevance_weight / total_weight
        self.recency_weight = recency_weight / total_weight
        self.importance_weight = importance_weight / total_weight
        self.min_similarity = min_similarity
        self._schema_ready = False

    def _connect(self) -> psycopg.Connection:
        try:
            conn = psycopg.connect(self.dsn, connect_timeout=5)
            register_vector(conn)
            if not self._schema_ready:
                schema_path = Path(__file__).with_name("schema.sql")
                try:
                    conn.execute(schema_path.read_text(encoding="utf-8"))
                    conn.commit()
                    self._schema_ready = True
                except Exception:
                    conn.close()
                    raise
            return conn
        except Exception as exc:
            raise MemoryStoreError(f"Memory database is unavailable: {exc}") from exc

    @staticmethod
    def _to_item(row: tuple) -> MemoryItem:
        metadata = row[8] if isinstance(row[8], dict) else json.loads(row[8] or "{}")
        return MemoryItem(
            memory_id=row[0],
            user_id=row[1],
            tier=MemoryTier(row[2]),
            content=row[3],
            importance=row[4],
            created_at=row[5],
            updated_at=row[6],
            source_turn=row[7],
            metadata=metadata,
        )

    def add(self, candidate: MemoryCandidate, user_id: str = "default") -> MemoryItem:
        vector = self.embedder.embed(candidate.content)
        memory_id = f"mem-{uuid.uuid4().hex[:8]}"
        now = datetime.now(UTC)
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    INSERT INTO memories
                        (memory_id, user_id, tier, content, importance, embedding,
                         created_at, updated_at, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING memory_id, user_id, tier, content, importance,
                              created_at, updated_at, source_turn, metadata
                    """,
                    (
                        memory_id,
                        user_id,
                        candidate.tier.value,
                        candidate.content,
                        candidate.importance,
                        vector,
                        now,
                        now,
                        json.dumps({"reason": candidate.reason}),
                    ),
                ).fetchone()
            return self._to_item(row)
        except MemoryStoreError:
            raise
        except Exception as exc:
            raise MemoryStoreError(f"Failed to add memory: {exc}") from exc

    def search(
        self,
        query: str,
        user_id: str = "default",
        tiers: list[MemoryTier] | None = None,
        top_k: int = 5,
    ) -> list[MemoryItem]:
        if top_k <= 0 or not query.strip() or tiers == []:
            return []
        vector = self.embedder.embed(query)
        tier_values = [tier.value for tier in tiers] if tiers else None
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    WITH scored AS (
                        SELECT memory_id, user_id, tier, content, importance,
                               created_at, updated_at, source_turn, metadata,
                               1 - (embedding <=> %s::vector) AS relevance,
                               exp(-greatest(extract(epoch FROM (NOW() - updated_at)), 0)
                                   / (30.0 * 86400.0)) AS recency
                        FROM memories
                        WHERE user_id = %s
                          AND (%s::text[] IS NULL OR tier = ANY(%s::text[]))
                    )
                    SELECT memory_id, user_id, tier, content, importance,
                           created_at, updated_at, source_turn, metadata
                    FROM scored
                    WHERE relevance >= %s
                    ORDER BY (%s * relevance + %s * recency + %s * importance) DESC,
                             updated_at DESC
                    LIMIT %s
                    """,
                    (
                        vector,
                        user_id,
                        tier_values,
                        tier_values,
                        self.min_similarity,
                        self.relevance_weight,
                        self.recency_weight,
                        self.importance_weight,
                        top_k,
                    ),
                ).fetchall()
            return [self._to_item(row) for row in rows]
        except MemoryStoreError:
            raise
        except Exception as exc:
            raise MemoryStoreError(f"Failed to search memories: {exc}") from exc

    def update(
        self,
        memory_id: str,
        content: str | None = None,
        importance: float | None = None,
    ) -> MemoryItem | None:
        if content is None and importance is None:
            return self._get_by_id(memory_id)
        vector = self.embedder.embed(content) if content is not None else None
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    UPDATE memories
                    SET content = COALESCE(%s, content),
                        embedding = COALESCE(%s::vector, embedding),
                        importance = COALESCE(%s, importance),
                        updated_at = NOW()
                    WHERE memory_id = %s
                    RETURNING memory_id, user_id, tier, content, importance,
                              created_at, updated_at, source_turn, metadata
                    """,
                    (content, vector, importance, memory_id),
                ).fetchone()
            return self._to_item(row) if row else None
        except MemoryStoreError:
            raise
        except Exception as exc:
            raise MemoryStoreError(f"Failed to update memory: {exc}") from exc

    def _get_by_id(self, memory_id: str) -> MemoryItem | None:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """SELECT memory_id, user_id, tier, content, importance,
                              created_at, updated_at, source_turn, metadata
                       FROM memories WHERE memory_id = %s""",
                    (memory_id,),
                ).fetchone()
            return self._to_item(row) if row else None
        except Exception as exc:
            raise MemoryStoreError(f"Failed to read memory: {exc}") from exc

    def delete(self, memory_id: str) -> bool:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "DELETE FROM memories WHERE memory_id = %s RETURNING memory_id",
                    (memory_id,),
                ).fetchone()
            return row is not None
        except Exception as exc:
            raise MemoryStoreError(f"Failed to delete memory: {exc}") from exc

    def list_all(self, user_id: str = "default") -> list[MemoryItem]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """SELECT memory_id, user_id, tier, content, importance,
                              created_at, updated_at, source_turn, metadata
                       FROM memories WHERE user_id = %s
                       ORDER BY updated_at DESC, memory_id""",
                    (user_id,),
                ).fetchall()
            return [self._to_item(row) for row in rows]
        except Exception as exc:
            raise MemoryStoreError(f"Failed to list memories: {exc}") from exc
