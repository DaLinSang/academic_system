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
from app.retrieval.study_intent import is_study_request


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

            by_page: dict[int, list[DocumentChunk]] = {}
            for chunk in chunks:
                by_page.setdefault(chunk.page_number, []).append(chunk)
            page_rows = [
                (resolved_document_id, chunks[0].file_name, page, self._rebuild_page(parts))
                for page, parts in sorted(by_page.items())
            ]

            with self._connect() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM document_pages WHERE document_id = %s",
                        (resolved_document_id,),
                    )
                    cursor.executemany(
                        """INSERT INTO document_pages
                           (document_id, file_name, page_number, text)
                           VALUES (%s, %s, %s, %s)
                           ON CONFLICT (document_id, page_number) DO UPDATE SET
                             file_name=EXCLUDED.file_name, text=EXCLUDED.text,
                             created_at=NOW()""",
                        page_rows,
                    )
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

    @staticmethod
    def _rebuild_page(chunks: list[DocumentChunk]) -> str:
        """Restore a page from overlapping chunks without duplicating overlap."""
        ordered = sorted(chunks, key=lambda c: (c.char_start or 0, c.chunk_id))
        if all(c.char_start is not None and c.char_end is not None for c in ordered):
            page_start = ordered[0].char_start
            pieces: list[str] = []
            end = page_start
            for chunk in ordered:
                if chunk.char_start > end:
                    # A gap means the source cannot be reconstructed faithfully.
                    raise RetrievalError(f"Incomplete page chunks: {chunk.file_name} p.{chunk.page_number}")
                skip = max(0, end - chunk.char_start)
                pieces.append(chunk.text[skip:])
                end = max(end, chunk.char_end)
            return "".join(pieces)
        # Older/imported chunks may have no offsets; preserve all content.
        return "\n".join(chunk.text for chunk in ordered)

    def read_document(self, file_name: str) -> str:
        """Return the persisted OCR/parsed text in page order.

        This is intentionally separate from retrieval: a request to summarize
        one selected PDF must not be limited by Top-K chunk ranking.
        """
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """SELECT page_number, text FROM document_pages
                       WHERE file_name = %s ORDER BY page_number""",
                    (file_name,),
                ).fetchall()
        except Exception as exc:
            raise RetrievalError(f"Failed to read document {file_name}: {exc}") from exc
        if not rows:
            raise RetrievalError(f"Document is not indexed: {file_name}")
        return "\n\n".join(f"[Page {page}]\n{text}" for page, text in rows)

    def delete_document(self, file_name: str) -> bool:
        """Delete a PDF and its persisted page cache by visible file name."""
        try:
            with self._connect() as conn:
                chunks = conn.execute("DELETE FROM document_chunks WHERE file_name=%s", (file_name,))
                pages = conn.execute("DELETE FROM document_pages WHERE file_name=%s", (file_name,))
                conn.commit()
            if not chunks.rowcount and not pages.rowcount:
                return False
            self._bm25_loaded = False
            self._bm25 = BM25Retriever()
            return True
        except Exception as exc:
            raise RetrievalError(f"Failed to delete document {file_name}: {exc}") from exc

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

    def _retrieve_documents(
        self, query: str, names: list[str], top_k: int, *, overview: bool | None = None
    ) -> list[Evidence]:
        """Bounded, source-scoped overview retrieval; never mix other PDFs."""
        query = expand_field_query(query)
        chunks = [c for c in self._bm25._chunks if c.file_name in names]
        scoped = BM25Retriever(chunks)
        if overview is None:
            overview = bool(re.search(
                r"总结|概括|综述|主要结论|研究目标|summari[sz]|overview", query, re.I
            ))
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

    def _retrieve_study_documents(
        self, query: str, names: list[str], top_k: int
    ) -> list[Evidence]:
        """Select different parts of each requested PDF within a bounded budget.

        The page range excludes a trailing bibliography when identifiable. It
        does not read an entire PDF into the LLM prompt or claim exhaustive
        coverage: each item remains an ordinary, verifiable Evidence chunk.
        """
        if len(names) > top_k:
            self.last_clarification = (
                f"本轮最多能引用 {top_k} 份文档，请减少要比较的文件数量。"
            )
            self.last_warning = self.last_clarification
            return []

        chunks_by_name: dict[str, list[DocumentChunk]] = {}
        for name in names:
            document_chunks = [c for c in self._bm25._chunks if c.file_name == name]
            document_chunks.sort(key=lambda c: (c.page_number, c.char_start or 0, c.chunk_id))
            bibliography_page = next(
                (c.page_number for c in document_chunks if re.search(
                    r"(?:^|\n)\s*(?:references|bibliography|参考文献)\s*(?:\n|$)",
                    c.text, re.I,
                )),
                None,
            )
            if bibliography_page:
                body = [c for c in document_chunks if c.page_number < bibliography_page]
                document_chunks = body or document_chunks
            chunks_by_name[name] = document_chunks

        vector_scores: dict[str, float] = {}
        try:
            vector = self.embedder.embed(query)
            with self._connect() as conn:
                rows = conn.execute(
                    """SELECT chunk_id, 1 - (embedding <=> %s::vector) AS score
                       FROM document_chunks WHERE file_name = ANY(%s)
                       ORDER BY embedding <=> %s::vector LIMIT %s""",
                    (vector, names, vector, max(top_k * 8, 32)),
                ).fetchall()
            vector_scores = {key: float(score) for key, score in rows}
        except Exception as exc:
            self.last_warning = f"Scoped vector retrieval unavailable; using text only: {exc}"

        query_terms = set(_tokenize(expand_field_query(query)))
        facets = (
            "abstract introduction motivation objective background",
            "method model approach architecture mechanism",
            "experiment dataset evaluation results findings",
            "results findings evidence analysis",
            "discussion limitations conclusion contributions",
            "conclusion takeaways applications implications future work",
        )
        chosen: dict[str, list[Evidence]] = {}
        for name in names:
            document_chunks = chunks_by_name[name]
            pages = sorted({c.page_number for c in document_chunks})
            target = (top_k // len(names)) + (names.index(name) < top_k % len(names))
            selected: list[DocumentChunk] = []
            used_pages: set[int] = set()
            slots = min(target, len(pages))
            for slot in range(slots):
                low = slot * len(pages) // slots
                high = (slot + 1) * len(pages) // slots
                page_set = set(pages[low:high])
                pool = [c for c in document_chunks if c.page_number in page_set]
                if len(pages) <= 20 and slot == 0:
                    abstract = [c for c in pool if re.search(
                        r"\babstract\b|\b摘要\b", c.text, re.I
                    )]
                    if abstract:
                        pool = abstract
                if len(pages) <= 20 and slot == slots - 1 and slots >= 3:
                    last_page = [c for c in pool if c.page_number == pages[-1]]
                    if last_page:
                        pool = last_page
                informative = [c for c in pool if len(c.text.strip()) >= 120]
                if informative:
                    pool = informative
                facet_terms = set(_tokenize(facets[min(slot * len(facets) // slots,
                                                       len(facets) - 1)]))

                def relevance(chunk: DocumentChunk,
                              terms: set[str] = facet_terms) -> tuple[float, int, int]:
                    tokens = set(_tokenize(chunk.text))
                    score = (
                        2 * len(query_terms & tokens)
                        + len(terms & tokens)
                        + max(0.0, vector_scores.get(chunk.chunk_id, 0.0))
                        + min(len(chunk.text), 800) / 800
                    )
                    return score, len(chunk.text), -chunk.page_number

                if pool:
                    best = max(pool, key=relevance)
                    selected.append(best)
                    used_pages.add(best.page_number)

            if len(selected) < target:
                for chunk in sorted(document_chunks,
                                    key=lambda c: (-len(c.text), c.page_number)):
                    if chunk.page_number not in used_pages:
                        selected.append(chunk)
                        used_pages.add(chunk.page_number)
                    if len(selected) >= target:
                        break
            chosen[name] = [Evidence.from_chunk(
                chunk, score=round(vector_scores.get(chunk.chunk_id, 0.0), 4),
                method=RetrievalMethod.VECTOR if chunk.chunk_id in vector_scores
                else RetrievalMethod.BM25,
            ) for chunk in selected]

        # Interleave documents so the answer model's first six evidence blocks
        # include each compared document, even when top_k is small.
        result: list[Evidence] = []
        for rank in range(max((len(items) for items in chosen.values()), default=0)):
            result.extend(items[rank] for items in chosen.values() if rank < len(items))
        return result[:top_k]

    def retrieve_for_claim(
        self, claim: str, file_names: list[str], top_k: int = 3
    ) -> list[Evidence]:
        """Find evidence for a claim exclusively in the named, indexed PDFs.

        Scope is applied before BM25 ranking and in the vector SQL query. An
        unknown name returns no evidence rather than falling back to the
        global corpus, even when another document contains a better match.
        """
        self.last_warning = None
        self.last_clarification = None
        if top_k <= 0 or not _tokenize(claim):
            return []
        if not isinstance(file_names, list) or not file_names or any(
            not isinstance(name, str) or not name for name in file_names
        ):
            raise ValueError("file_names must be a nonempty list of PDF file names")

        self._load_bm25_index()
        available = {chunk.file_name for chunk in self._bm25._chunks}
        missing = sorted(set(file_names) - available)
        if missing:
            self.last_warning = f"PDF not indexed: {', '.join(missing)}"
            return []
        return self._retrieve_documents(
            claim, list(dict.fromkeys(file_names)), top_k, overview=False
        )

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
            if is_study_request(query) or len(scope.names) > 1:
                return self._retrieve_study_documents(query, list(scope.names), top_k)
            return self._retrieve_documents(query, list(scope.names), top_k)
        if is_study_request(query) and re.search(
            r"这(?:份|篇|个).{0,8}(?:PDF|文档|论文|讲义)|"
            r"(?:this|the)\s+(?:pdf|paper|document)|"
            r"(?:比较|对比|compare).*(?:文档|论文|PDF|paper|document)|"
            r"复习|提纲",
            query, re.I,
        ):
            names = sorted({c.file_name for c in self._bm25._chunks})
            if len(names) == 1:
                return self._retrieve_study_documents(query, names, top_k)
            self.last_clarification = (
                "文档库中有多份 PDF，请在问题中写出要总结或比较的文件名。"
            )
            self.last_warning = self.last_clarification
            return []
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
