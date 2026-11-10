"""A small in-memory keyword retriever for the first retrieval milestone."""

from __future__ import annotations

import re

from app.core.types import DocumentChunk, Evidence, RetrievalMethod
from app.ingestion.pdf_parser import parse_pdf

_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")


def _tokenize(text: str) -> set[str]:
    """Convert English and Chinese text into comparable lowercase tokens."""
    return {token.lower() for token in _TOKEN_PATTERN.findall(text)}


class KeywordRetriever:
    """Retrieve indexed chunks using simple keyword overlap.

    This class is intentionally an in-memory implementation. It provides a
    real implementation of the Retriever contract before BM25 and vector
    retrieval are introduced in later milestones.
    """

    def __init__(self, chunks: list[DocumentChunk] | None = None) -> None:
        self._documents: dict[str, list[DocumentChunk]] = {}
        if chunks:
            self._replace_document_chunks(chunks)

    def _replace_document_chunks(self, chunks: list[DocumentChunk]) -> None:
        """Replace the indexed chunks belonging to each document."""
        grouped: dict[str, list[DocumentChunk]] = {}
        for chunk in chunks:
            grouped.setdefault(chunk.document_id, []).append(chunk)

        self._documents.update(grouped)

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

        return len(chunks)

    def retrieve(self, query: str, top_k: int = 5) -> list[Evidence]:
        """Return the most relevant indexed chunks as Evidence objects."""
        if top_k <= 0:
            return []

        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        scored_chunks: list[tuple[float, int, DocumentChunk]] = []
        position = 0

        for document_chunks in self._documents.values():
            for chunk in document_chunks:
                chunk_tokens = _tokenize(chunk.text)
                overlap = query_tokens & chunk_tokens
                score = len(overlap) / len(query_tokens)
                if score > 0:
                    scored_chunks.append((score, position, chunk))
                position += 1

        scored_chunks.sort(key=lambda item: (-item[0], item[1]))

        return [
            Evidence(
                chunk_id=chunk.chunk_id,
                document_id=chunk.document_id,
                file_name=chunk.file_name,
                page_number=chunk.page_number,
                text=chunk.text,
                score=round(score, 4),
                retrieval_method=RetrievalMethod.MOCK,
            )
            for score, _, chunk in scored_chunks[:top_k]
        ]
