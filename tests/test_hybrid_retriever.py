"""Tests for hybrid retrieval and RRF fusion."""

from __future__ import annotations

import pytest

from app.core.errors import IngestionError, RetrievalError
from app.core.types import DocumentChunk, Evidence, RetrievalMethod
from app.retrieval import hybrid_retriever
from app.retrieval.hybrid_retriever import HybridRetriever


class FakeEmbedder:
    """Deterministic embedder for tests."""

    def embed(self, text: str) -> list[float]:
        return [float(len(text)), 1.0]


def make_chunk(chunk_id: str, text: str, page_number: int) -> DocumentChunk:
    """Create a test chunk."""
    return DocumentChunk(
        chunk_id=chunk_id,
        document_id="demo-paper",
        file_name="demo.pdf",
        page_number=page_number,
        text=text,
    )


def make_evidence(chunk_id: str, text: str, page_number: int) -> Evidence:
    """Create a test evidence item."""
    return Evidence(
        chunk_id=chunk_id,
        document_id="demo-paper",
        file_name="demo.pdf",
        page_number=page_number,
        text=text,
        score=1.0,
        retrieval_method=RetrievalMethod.BM25,
    )


def test_retrieve_fuses_bm25_and_vector_results(monkeypatch) -> None:
    retriever = HybridRetriever(embedder=FakeEmbedder())
    bm25_results = [
        make_evidence("chunk-a", "retrieval text", 1),
        make_evidence("chunk-b", "other text", 2),
    ]
    vector_results = [("chunk-b", 0.9), ("chunk-c", 0.8)]

    monkeypatch.setattr(retriever._bm25, "retrieve", lambda query, top_k: bm25_results)
    monkeypatch.setattr(
        retriever,
        "_vector_results",
        lambda query_embedding, limit: vector_results,
    )
    monkeypatch.setattr(
        retriever,
        "_connect",
        lambda: FakeEvidenceConnection(),
    )
    monkeypatch.setattr(retriever, "_load_bm25_index", lambda: None)

    results = retriever.retrieve("retrieval", top_k=3)

    assert [result.chunk_id for result in results] == ["chunk-b", "chunk-a", "chunk-c"]
    assert all(result.retrieval_method == RetrievalMethod.RRF for result in results)
    assert results[0].file_name == "demo.pdf"
    assert results[0].page_number == 2


def test_retrieve_returns_empty_for_empty_query() -> None:
    retriever = HybridRetriever(embedder=FakeEmbedder())

    assert retriever.retrieve("", top_k=5) == []
    assert retriever.retrieve("retrieval", top_k=0) == []


def test_retrieve_uses_bm25_when_ollama_fails(monkeypatch) -> None:
    class FailingEmbedder:
        def embed(self, text: str) -> list[float]:
            raise RetrievalError("Ollama unavailable")

    retriever = HybridRetriever(embedder=FailingEmbedder())
    monkeypatch.setattr(retriever, "_load_bm25_index", lambda: None)
    monkeypatch.setattr(
        retriever._bm25,
        "retrieve",
        lambda query, top_k: [make_evidence("chunk-a", "retrieval text", 1)],
    )

    results = retriever.retrieve("retrieval", top_k=2)

    assert [result.chunk_id for result in results] == ["chunk-a"]
    assert results[0].retrieval_method == RetrievalMethod.BM25
    assert "Ollama unavailable" in retriever.last_warning


def test_retrieve_clears_warning_on_next_query(monkeypatch) -> None:
    retriever = HybridRetriever(embedder=FakeEmbedder())
    retriever.last_warning = "previous failure"
    monkeypatch.setattr(retriever, "_load_bm25_index", lambda: None)
    monkeypatch.setattr(retriever._bm25, "retrieve", lambda query, top_k: [])
    monkeypatch.setattr(retriever, "_vector_results", lambda embedding, limit: [])

    assert retriever.retrieve("retrieval") == []
    assert retriever.last_warning is None


def test_load_bm25_index_rebuilds_from_persisted_chunks(monkeypatch) -> None:
    retriever = HybridRetriever(embedder=FakeEmbedder())
    chunks = [make_chunk("chunk-a", "persisted retrieval content", 1)]
    monkeypatch.setattr(retriever, "_connect", lambda: FakeCorpusConnection(chunks))

    retriever._load_bm25_index()
    results = retriever._bm25.retrieve("persisted", top_k=5)

    assert [item.chunk_id for item in results] == ["chunk-a"]
    assert retriever._bm25_loaded is True


def test_vector_query_casts_embedding_parameter_to_vector(monkeypatch) -> None:
    retriever = HybridRetriever(embedder=FakeEmbedder())
    connection = FakeVectorConnection()
    monkeypatch.setattr(retriever, "_connect", lambda: connection)

    assert retriever._vector_results([0.1, 0.2], limit=3) == []
    assert "<=> %s::vector" in connection.query
    assert connection.params == ([0.1, 0.2], [0.1, 0.2], 3)


def test_index_document_invalidates_bm25_cache(monkeypatch) -> None:
    retriever = HybridRetriever(embedder=FakeEmbedder())
    retriever._bm25_loaded = True
    chunk = make_chunk("demo-paper:p1:c0", "new text", 1)
    monkeypatch.setattr(hybrid_retriever, "parse_pdf", lambda *args, **kwargs: [chunk])
    monkeypatch.setattr(retriever, "_connect", lambda: FakeIndexConnection())

    assert retriever.index_document("demo.pdf") == 1
    assert retriever._bm25_loaded is False


def test_index_document_rejects_image_pdf_without_ocr(monkeypatch) -> None:
    retriever = HybridRetriever(embedder=FakeEmbedder())
    monkeypatch.setattr(hybrid_retriever, "parse_pdf", lambda *args, **kwargs: [])

    with pytest.raises(IngestionError, match="No searchable text"):
        retriever.index_document("scanned.pdf")


class FakeEvidenceConnection:
    """Minimal connection double for loading vector-only evidence."""

    def __enter__(self) -> FakeEvidenceConnection:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None

    def execute(
        self,
        query: str,
        params: tuple[object, ...] = (),
    ) -> FakeEvidenceCursor:
        return FakeEvidenceCursor()


class FakeEvidenceCursor:
    """Minimal cursor double returning one vector-only chunk."""

    def fetchone(self) -> tuple[str, str, str, int, str]:
        return ("chunk-c", "demo-paper", "demo.pdf", 3, "vector-only text")

    def fetchall(self) -> list[tuple[str, str, str, int, str]]:
        return [("chunk-c", "demo-paper", "demo.pdf", 3, "vector-only text")]


class FakeIndexConnection:
    def __enter__(self) -> FakeIndexConnection:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None

    def cursor(self) -> FakeIndexConnection:
        return self

    def execute(self, query: str, params: object = None) -> None:
        return None

    def executemany(self, query: str, rows: object) -> None:
        return None

    def commit(self) -> None:
        return None


class FakeCorpusConnection:
    """Connection double returning persisted chunk rows."""

    def __init__(self, chunks: list[DocumentChunk]) -> None:
        self.chunks = chunks

    def __enter__(self) -> FakeCorpusConnection:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None

    def execute(self, query: str) -> FakeCorpusCursor:
        return FakeCorpusCursor(self.chunks)


class FakeCorpusCursor:
    """Cursor double converting chunks into database-shaped rows."""

    def __init__(self, chunks: list[DocumentChunk]) -> None:
        self.rows = [
            (
                chunk.chunk_id,
                chunk.document_id,
                chunk.file_name,
                chunk.page_number,
                chunk.text,
                chunk.char_start,
                chunk.char_end,
            )
            for chunk in chunks
        ]

    def fetchall(self) -> list[tuple[str, str, str, int, str, int | None, int | None]]:
        return self.rows


class FakeVectorConnection:
    """Connection double recording the pgvector query and parameters."""

    def __init__(self) -> None:
        self.query = ""
        self.params: tuple[object, ...] = ()

    def __enter__(self) -> FakeVectorConnection:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None

    def execute(self, query: str, params: tuple[object, ...]) -> FakeVectorCursor:
        self.query = query
        self.params = params
        return FakeVectorCursor()


class FakeVectorCursor:
    """Cursor double returning no matching rows."""

    def fetchall(self) -> list[tuple[str, float]]:
        return []
