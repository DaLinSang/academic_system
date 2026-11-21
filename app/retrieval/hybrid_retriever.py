"""Hybrid BM25 and pgvector retrieval with reciprocal rank fusion."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol

import httpx
import psycopg
from pgvector.psycopg import register_vector

from app.core.config import settings
from app.core.errors import IngestionError, RetrievalError
from app.core.types import DocumentChunk, Evidence, RetrievalMethod
from app.ingestion.pdf_parser import parse_pdf
from app.retrieval.bm25_retriever import BM25Retriever, _tokenize
from app.retrieval.document_scope import expand_field_query, resolve_scope


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
                trust_env=False,
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
        self._bm25_loaded = False
        self.last_warning: str | None = None
        self.last_clarification: str | None = None

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

    def _load_bm25_index(self) -> None:
        """Load persisted chunks once so BM25 works across process restarts."""
        if self._bm25_loaded:
            return

        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT chunk_id, document_id, file_name, page_number,
                           text, char_start, char_end
                    FROM document_chunks
                    ORDER BY document_id, page_number, char_start, chunk_id
                    """
                ).fetchall()
        except RetrievalError:
            raise
        except Exception as exc:
            raise RetrievalError(f"Failed to load BM25 corpus: {exc}") from exc

        chunks = [
            DocumentChunk(
                chunk_id=row[0],
                document_id=row[1],
                file_name=row[2],
                page_number=row[3],
                text=row[4],
                char_start=row[5],
                char_end=row[6],
            )
            for row in rows
        ]
        self._bm25 = BM25Retriever(chunks)
        self._bm25_loaded = True

    def index_document(
        self,
        pdf_path: str,
        document_id: str | None = None,
    ) -> int:
        """Parse, embed, and upsert one PDF into the vector index."""
        chunks = parse_pdf(pdf_path, document_id=document_id)
        if not chunks:
            raise IngestionError(
                "No searchable text found in PDF. For scanned pages, install "
                "pdftoppm and tesseract, then re-index the document."
            )
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

        # The current instance may not have loaded other persisted PDFs yet.
        # Reload the full corpus on the next query to avoid hiding them.
        self._bm25_loaded = False
        return len(chunks)

    def list_indexed_documents(self) -> list[dict[str, object]]:
        """Return read-only metadata for documents stored in PostgreSQL.

        This optional concrete capability avoids changing the shared Retriever
        Protocol while allowing the UI to survive Streamlit session resets.
        """
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT file_name, COUNT(*) AS chunk_count,
                           COUNT(DISTINCT page_number) AS page_count,
                           MIN(page_number) AS first_page,
                           MAX(page_number) AS last_page,
                           MAX(created_at) AS indexed_at
                    FROM document_chunks
                    GROUP BY file_name
                    ORDER BY file_name
                    """
                ).fetchall()
        except RetrievalError:
            raise
        except Exception as exc:
            raise RetrievalError(
                f"Failed to list indexed documents: {exc}"
            ) from exc

        return [
            {
                "file_name": str(row[0]),
                "chunks": int(row[1]),
                "page_count": int(row[2]),
                "first_page": int(row[3]),
                "last_page": int(row[4]),
                "indexed_at": (
                    row[5].isoformat() if row[5] is not None else None
                ),
            }
            for row in rows
        ]

    def _vector_results(
        self,
        query_embedding: list[float],
        limit: int,
    ) -> list[tuple[str, float]]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT chunk_id, 1 - (embedding <=> %s::vector) AS score
                    FROM document_chunks
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (query_embedding, query_embedding, limit),
                ).fetchall()
            return [(row[0], float(row[1])) for row in rows]
        except RetrievalError:
            raise
        except Exception as exc:
            raise RetrievalError(f"Vector retrieval failed: {exc}") from exc

    def _retrieve_documents(self, query: str, names: list[str], top_k: int) -> list[Evidence]:
        """Bounded, source-scoped overview retrieval; never mix other PDFs."""
        query = expand_field_query(query)
        chunks = [c for c in self._bm25._chunks if c.file_name in names]
        scoped = BM25Retriever(chunks)
        overview = bool(re.search(r"总结|概括|综述|主要结论|研究目标|summari[sz]|overview", query, re.I))
        queries = [query]
        if overview:
            queries += ["abstract introduction motivation objective 研究目标",
                        "method approach 方法", "experiment results 实验结果",
                        "conclusion discussion limitation 结论"]
        rankings = [scoped.retrieve(q, top_k=max(top_k * 4, 20)) for q in queries]
        try:
            vector = self.embedder.embed(query)
            with self._connect() as conn:
                rows = conn.execute(
                    """SELECT chunk_id, 1 - (embedding <=> %s::vector) AS score
                       FROM document_chunks WHERE file_name = ANY(%s)
                       ORDER BY embedding <=> %s::vector LIMIT %s""",
                    (vector, names, vector, max(top_k * 4, 20)),
                ).fetchall()
            by_id = {c.chunk_id: c for c in chunks}
            vector_evidence = [Evidence(
                chunk_id=key, document_id=by_id[key].document_id,
                file_name=by_id[key].file_name, page_number=by_id[key].page_number,
                text=by_id[key].text, score=float(score),
                retrieval_method=RetrievalMethod.VECTOR,
            ) for key, score in rows if key in by_id]
            rankings.insert(0, vector_evidence)
        except Exception as exc:
            self.last_warning = f"Scoped vector retrieval unavailable; using BM25 only: {exc}"
        selected: list[Evidence] = []
        if overview:
            # Prefer actual section headings over citations mentioning these words.
            for heading in (r"abstract|摘要", r"introduction|引言", r"methods?|方法",
                            r"results?|experimental results?|实验结果", r"conclusions?|结论"):
                pattern = re.compile(r"^\s*(?:\d+(?:\.\d+)*[. ]*\s*)?(?:" + heading + r")\s*$", re.I | re.M)
                for chunk in chunks:
                    if pattern.search(chunk.text) and not any(
                        e.document_id == chunk.document_id and e.page_number == chunk.page_number
                        for e in selected
                    ):
                        selected.append(Evidence(
                            chunk_id=chunk.chunk_id, document_id=chunk.document_id,
                            file_name=chunk.file_name, page_number=chunk.page_number,
                            text=chunk.text, score=0.0, retrieval_method=RetrievalMethod.BM25,
                        ))
                        break
            selected = selected[:top_k]

        seen = {e.chunk_id for e in selected}
        pages = {(e.document_id, e.page_number) for e in selected}
        if len(selected) >= top_k:
            return selected
        # Round-robin facets prevent the opening section from consuming the budget.
        for rank in range(max((len(r) for r in rankings), default=0)):
            for results in rankings:
                if rank >= len(results):
                    continue
                item = results[rank]
                page = (item.document_id, item.page_number)
                if item.chunk_id in seen or (overview and page in pages):
                    continue
                selected.append(item)
                seen.add(item.chunk_id)
                pages.add(page)
                if len(selected) >= top_k:
                    return selected
        return selected

    def retrieve(self, query: str, top_k: int = 5) -> list[Evidence]:
        """Return BM25 and vector results fused with reciprocal rank fusion."""
        self.last_warning = None
        self.last_clarification = None
        if top_k <= 0 or not _tokenize(query):
            return []

        self._load_bm25_index()
        scope = resolve_scope(query, self._bm25._chunks)
        if scope.clarification:
            self.last_clarification = scope.clarification
            self.last_warning = scope.clarification
            return []
        if scope.names:
            return self._retrieve_documents(query, list(scope.names), top_k)
        bm25_results = self._bm25.retrieve(query, top_k=max(top_k * 4, 20))
        try:
            vector_results = self._vector_results(
                self.embedder.embed(query),
                limit=max(top_k * 4, 20),
            )
        except RetrievalError as exc:
            self.last_warning = f"Vector retrieval unavailable; using BM25 only: {exc}"
            return bm25_results[:top_k]

        evidence_by_id: dict[str, Evidence] = {
            result.chunk_id: result for result in bm25_results
        }
        vector_scores = {chunk_id: score for chunk_id, score in vector_results}

        missing_ids = [
            chunk_id for chunk_id, _ in vector_results if chunk_id not in evidence_by_id
        ]
        if missing_ids:
            try:
                with self._connect() as conn:
                    rows = conn.execute(
                        """
                        SELECT chunk_id, document_id, file_name, page_number, text
                        FROM document_chunks
                        WHERE chunk_id = ANY(%s)
                        """,
                        (missing_ids,),
                    ).fetchall()
            except RetrievalError:
                raise
            except Exception as exc:
                raise RetrievalError(f"Failed to load vector evidence: {exc}") from exc

            for row in rows:
                evidence_by_id[row[0]] = Evidence(
                    chunk_id=row[0],
                    document_id=row[1],
                    file_name=row[2],
                    page_number=row[3],
                    text=row[4],
                    score=vector_scores[row[0]],
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
