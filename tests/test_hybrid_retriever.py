"""Tests for hybrid retrieval and RRF fusion."""

from __future__ import annotations

from app.core.types import DocumentChunk, Evidence, RetrievalMethod
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

    results = retriever.retrieve("retrieval", top_k=3)

    assert [result.chunk_id for result in results] == ["chunk-b", "chunk-a", "chunk-c"]
    assert all(result.retrieval_method == RetrievalMethod.RRF for result in results)
    assert results[0].file_name == "demo.pdf"
    assert results[0].page_number == 2


def test_retrieve_returns_empty_for_empty_query() -> None:
    retriever = HybridRetriever(embedder=FakeEmbedder())

    assert retriever.retrieve("", top_k=5) == []
    assert retriever.retrieve("retrieval", top_k=0) == []


class FakeEvidenceConnection:
    """Minimal connection double for loading vector-only evidence."""

    def __enter__(self) -> FakeEvidenceConnection:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None

    def execute(self, query: str, params: tuple[str]) -> FakeEvidenceCursor:
        return FakeEvidenceCursor()


class FakeEvidenceCursor:
    """Minimal cursor double returning one vector-only chunk."""

    def fetchone(self) -> tuple[str, str, str, int, str]:
        return ("chunk-c", "demo-paper", "demo.pdf", 3, "vector-only text")
