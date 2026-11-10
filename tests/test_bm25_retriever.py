"""Tests for the BM25 retrieval implementation."""

from __future__ import annotations

from app.core.types import DocumentChunk, RetrievalMethod
from app.retrieval import bm25_retriever
from app.retrieval.bm25_retriever import BM25Retriever


def make_chunk(
    chunk_id: str,
    text: str,
    page_number: int,
    document_id: str = "demo-paper",
) -> DocumentChunk:
    """Create a test document chunk."""
    return DocumentChunk(
        chunk_id=chunk_id,
        document_id=document_id,
        file_name="demo.pdf",
        page_number=page_number,
        text=text,
    )


def test_retrieve_returns_bm25_evidence_in_score_order() -> None:
    retriever = BM25Retriever(
        [
            make_chunk(
                "demo-paper:p1:c0",
                "retrieval augmented generation uses external documents",
                page_number=1,
            ),
            make_chunk(
                "demo-paper:p2:c0",
                "self rag decides whether retrieval is necessary",
                page_number=2,
            ),
        ]
    )

    results = retriever.retrieve("external documents", top_k=5)

    assert len(results) == 1
    assert results[0].chunk_id == "demo-paper:p1:c0"
    assert results[0].file_name == "demo.pdf"
    assert results[0].page_number == 1
    assert results[0].retrieval_method == RetrievalMethod.BM25


def test_retrieve_returns_empty_list_when_nothing_matches() -> None:
    retriever = BM25Retriever(
        [make_chunk("demo-paper:p1:c0", "retrieval documents", page_number=1)]
    )

    assert retriever.retrieve("postgres database", top_k=5) == []


def test_retrieve_respects_top_k() -> None:
    retriever = BM25Retriever(
        [
            make_chunk("demo-paper:p1:c0", "retrieval documents", page_number=1),
            make_chunk("demo-paper:p2:c0", "retrieval systems", page_number=2),
            make_chunk("demo-paper:p3:c0", "retrieval evaluation", page_number=3),
        ]
    )

    results = retriever.retrieve("retrieval", top_k=2)

    assert len(results) == 2


def test_retrieve_handles_empty_query_and_empty_index() -> None:
    empty_retriever = BM25Retriever()

    assert empty_retriever.retrieve("retrieval") == []

    retriever = BM25Retriever(
        [make_chunk("demo-paper:p1:c0", "retrieval documents", page_number=1)]
    )

    assert retriever.retrieve("", top_k=5) == []
    assert retriever.retrieve("retrieval", top_k=0) == []


def test_index_document_uses_pdf_parser_and_returns_chunk_count(monkeypatch) -> None:
    chunks = [
        make_chunk("demo-paper:p1:c0", "retrieval documents", page_number=1),
        make_chunk("demo-paper:p2:c0", "retrieval systems", page_number=2),
    ]

    monkeypatch.setattr(
        bm25_retriever,
        "parse_pdf",
        lambda pdf_path, document_id=None: chunks,
    )

    retriever = BM25Retriever()

    assert retriever.index_document("demo.pdf", "demo-paper") == 2
    assert len(retriever.retrieve("systems")) == 1


def test_index_document_replaces_previous_import(monkeypatch) -> None:
    first_chunks = [
        make_chunk("demo-paper:p1:c0", "old content", page_number=1),
    ]
    second_chunks = [
        make_chunk("demo-paper:p1:c0", "new content", page_number=1),
    ]
    imported_chunks = [first_chunks, second_chunks]

    monkeypatch.setattr(
        bm25_retriever,
        "parse_pdf",
        lambda pdf_path, document_id=None: imported_chunks.pop(0),
    )

    retriever = BM25Retriever()
    retriever.index_document("demo.pdf", "demo-paper")
    retriever.index_document("demo.pdf", "demo-paper")

    assert retriever.retrieve("old") == []
    assert len(retriever.retrieve("new")) == 1
