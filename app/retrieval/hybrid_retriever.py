"""Hybrid BM25 and pgvector retrieval with reciprocal rank fusion."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import httpx
import psycopg
from pgvector.psycopg import register_vector

from app.core.config import settings
from app.core.errors import RetrievalError
from app.core.types import Evidence, RetrievalMethod
from app.ingestion.pdf_parser import parse_pdf
from app.retrieval.bm25_retriever import BM25Retriever, _tokenize


class Embedder(Protocol):
    """Embedding provider used by the hybrid retriever."""

    def embed(self, text: str) -> list[float]:
        ...


class OllamaEmbedder:
    """Generate embeddings through the configured local Ollama service."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = (base_url or settings.embedding.ollama_base_url).rstrip("/")
        self.model = model or settings.embedding.model
        self.timeout = timeout

    def embed(self, text: str) -> list[float]:
        try:
            response = httpx.post(
                f"{self.base_url}/api/embeddings",
                json={"model": self.model, "prompt": text},
                timeout=self.timeout,
            )
            response.raise_for_status()
            embedding = response.json().get("embedding")
        except Exception as exc:
            raise RetrievalError(f"Embedding service failed: {exc}") from exc

        if not isinstance(embedding, list) or not embedding:
            raise RetrievalError("Embedding service returned an empty vector")

        return [float(value) for value in embedding]


class HybridRetriever:
    """Retrieve evidence with BM25, pgvector, and RRF fusion."""

    def __init__(
        self,
        dsn: str | None = None,
        embedder: Embedder | None = None,
        rrf_k: int = 60,
        bm25_weight: float = 1.0,
        vector_weight: float = 1.0,
    ) -> None:
        if rrf_k <= 0:
            raise ValueError("rrf_k must be greater than zero")

        self.dsn = dsn or settings.db.dsn
        self.embedder = embedder or OllamaEmbedder()
        self.rrf_k = rrf_k
        self.bm25_weight = bm25_weight
        self.vector_weight = vector_weight
        self._bm25 = BM25Retriever()

    def _ensure_schema(self, conn: psycopg.Connection) -> None:
        schema_path = Path(__file__).with_name("schema.sql")
        conn.execute(schema_path.read_text(encoding="utf-8"))

    def _connect(self) -> psycopg.Connection:
        try:
            conn = psycopg.connect(self.dsn, connect_timeout=5)
            register_vector(conn)
            self._ensure_schema(conn)
            return conn
        except Exception as exc:
            raise RetrievalError(f"Retrieval database is unavailable: {exc}") from exc

    def index_document(
        self,
        pdf_path: str,
        document_id: str | None = None,
    ) -> int:
        """Parse, embed, and upsert one PDF into the vector index."""
        chunks = parse_pdf(pdf_path, document_id=document_id)
        resolved_document_id = document_id or Path(pdf_path).stem

        try:
            rows = [
                (
                    chunk.chunk_id,
                    chunk.document_id,
                    chunk.file_name,
                    chunk.page_number,
                    chunk.text,
                    chunk.char_start,
                    chunk.char_end,
                    self.embedder.embed(chunk.text),
                )
                for chunk in chunks
            ]

            with self._connect() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM document_chunks WHERE document_id = %s",
                        (resolved_document_id,),
                    )
                    cursor.executemany(
                        """
                        INSERT INTO document_chunks
                            (chunk_id, document_id, file_name, page_number,
                             text, char_start, char_end, embedding)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (chunk_id) DO UPDATE SET
                            document_id = EXCLUDED.document_id,
                            file_name = EXCLUDED.file_name,
                            page_number = EXCLUDED.page_number,
                            text = EXCLUDED.text,
                            char_start = EXCLUDED.char_start,
                            char_end = EXCLUDED.char_end,
                            embedding = EXCLUDED.embedding
                        """,
                        rows,
                    )
                conn.commit()
        except RetrievalError:
            raise
        except Exception as exc:
            raise RetrievalError(f"Failed to index document: {exc}") from exc

        self._bm25._documents[resolved_document_id] = chunks
        self._bm25._rebuild_index()
        return len(chunks)

    def _vector_results(
        self,
        query_embedding: list[float],
        limit: int,
    ) -> list[tuple[str, float]]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT chunk_id, 1 - (embedding <=> %s) AS score
                    FROM document_chunks
                    ORDER BY embedding <=> %s
                    LIMIT %s
                    """,
                    (query_embedding, query_embedding, limit),
                ).fetchall()
            return [(row[0], float(row[1])) for row in rows]
        except RetrievalError:
            raise
        except Exception as exc:
            raise RetrievalError(f"Vector retrieval failed: {exc}") from exc

    def retrieve(self, query: str, top_k: int = 5) -> list[Evidence]:
        """Return BM25 and vector results fused with reciprocal rank fusion."""
        if top_k <= 0 or not _tokenize(query):
            return []

        bm25_results = self._bm25.retrieve(query, top_k=max(top_k * 4, 20))
        vector_results = self._vector_results(
            self.embedder.embed(query),
            limit=max(top_k * 4, 20),
        )

        evidence_by_id: dict[str, Evidence] = {
            result.chunk_id: result for result in bm25_results
        }
        vector_scores = {chunk_id: score for chunk_id, score in vector_results}

        for chunk_id, _ in vector_results:
            if chunk_id in evidence_by_id:
                continue

            try:
                with self._connect() as conn:
                    row = conn.execute(
                        """
                        SELECT chunk_id, document_id, file_name, page_number, text
                        FROM document_chunks
                        WHERE chunk_id = %s
                        """,
                        (chunk_id,),
                    ).fetchone()
            except RetrievalError:
                raise
            except Exception as exc:
                raise RetrievalError(f"Failed to load vector evidence: {exc}") from exc

            if row is not None:
                evidence_by_id[row[0]] = Evidence(
                    chunk_id=row[0],
                    document_id=row[1],
                    file_name=row[2],
                    page_number=row[3],
                    text=row[4],
                    score=vector_scores[chunk_id],
                    retrieval_method=RetrievalMethod.VECTOR,
                )

        fused_scores: dict[str, float] = {}
        for rank, result in enumerate(bm25_results, start=1):
            fused_scores[result.chunk_id] = fused_scores.get(result.chunk_id, 0.0) + (
                self.bm25_weight / (self.rrf_k + rank)
            )

        for rank, (chunk_id, _) in enumerate(vector_results, start=1):
            fused_scores[chunk_id] = fused_scores.get(chunk_id, 0.0) + (
                self.vector_weight / (self.rrf_k + rank)
            )

        ranked_ids = sorted(
            fused_scores,
            key=lambda chunk_id: (-fused_scores[chunk_id], chunk_id),
        )[:top_k]

        return [
            evidence_by_id[chunk_id].model_copy(
                update={
                    "score": round(fused_scores[chunk_id], 6),
                    "retrieval_method": RetrievalMethod.RRF,
                }
            )
            for chunk_id in ranked_ids
            if chunk_id in evidence_by_id
        ]
