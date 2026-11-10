"""BM25 retrieval implementation for indexed document chunks."""

from __future__ import annotations

import re

from rank_bm25 import BM25Okapi

from app.core.types import DocumentChunk, Evidence, RetrievalMethod
from app.ingestion.pdf_parser import parse_pdf

_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")


def _tokenize(text: str) -> list[str]:
    """Tokenize English and Chinese text for BM25."""
    return [token.lower() for token in _TOKEN_PATTERN.findall(text)]


class BM25Retriever:
    """Retrieve indexed chunks with the Okapi BM25 ranking algorithm."""

    def __init__(self, chunks: list[DocumentChunk] | None = None) -> None:
        self._documents: dict[str, list[DocumentChunk]] = {}
        self._chunks: list[DocumentChunk] = []
        self._tokenized_corpus: list[list[str]] = []
        self._index: BM25Okapi | None = None

        if chunks:
            self._replace_document_chunks(chunks)

    def _replace_document_chunks(self, chunks: list[DocumentChunk]) -> None:
        """Replace the indexed chunks and rebuild the BM25 index."""
        grouped: dict[str, list[DocumentChunk]] = {}
        for chunk in chunks:
            grouped.setdefault(chunk.document_id, []).append(chunk)

        self._documents.update(grouped)
        self._rebuild_index()

    def _rebuild_index(self) -> None:
        """Rebuild the in-memory BM25 index from all indexed documents."""
        self._chunks = [
            chunk
            for document_chunks in self._documents.values()
            for chunk in document_chunks
        ]
        self._tokenized_corpus = [_tokenize(chunk.text) for chunk in self._chunks]
        self._index = BM25Okapi(self._tokenized_corpus) if self._chunks else None

    def index_document(
        self,
        pdf_path: str,
        document_id: str | None = None,
    ) -> int:
        """Parse and index one PDF, replacing an earlier import of that document."""
        chunks = parse_pdf(pdf_path, document_id=document_id)
        resolved_document_id = document_id

        if resolved_document_id is None:
            resolved_document_id = chunks[0].document_id if chunks else None

        if resolved_document_id is not None:
            self._documents[resolved_document_id] = chunks
            self._rebuild_index()

        return len(chunks)

    def retrieve(self, query: str, top_k: int = 5) -> list[Evidence]:
        """Return the top matching chunks as Evidence objects."""
        if top_k <= 0 or self._index is None:
            return []

        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        scores = self._index.get_scores(query_tokens)
        ranked_indices = sorted(
            range(len(self._chunks)),
            key=lambda index: (-scores[index], index),
        )

        results: list[Evidence] = []
        query_token_set = set(query_tokens)

        for index in ranked_indices:
            chunk_tokens = set(self._tokenized_corpus[index])
            if not query_token_set.intersection(chunk_tokens):
                continue

            chunk = self._chunks[index]
            results.append(
                Evidence(
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    file_name=chunk.file_name,
                    page_number=chunk.page_number,
                    text=chunk.text,
                    score=round(float(scores[index]), 4),
                    retrieval_method=RetrievalMethod.BM25,
                )
            )

            if len(results) >= top_k:
                break

        return results
