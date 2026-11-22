"""A named chapter must never borrow evidence from a different chapter."""

from app.core.errors import RetrievalError
from app.core.types import DocumentChunk
from app.retrieval.bm25_retriever import BM25Retriever
from app.retrieval.hybrid_retriever import HybridRetriever


class Offline:
    def embed(self, text):
        raise RetrievalError("offline")


def test_chapter_number_and_topic_are_both_required():
    chunks = [
        DocumentChunk(chunk_id="ten", document_id="ten", file_name="Chapter10-Network.pdf",
                      page_number=1, text="Association Rule appears in a comparison."),
        DocumentChunk(chunk_id="two", document_id="two", file_name="Chapter2-AssociationRule.pdf",
                      page_number=1, text="Association rules, support and confidence."),
    ]
    retriever = HybridRetriever(embedder=Offline())
    retriever._bm25 = BM25Retriever(chunks)
    retriever._bm25_loaded = True

    for query in ("根据 Chapter 2 Association Rule 整理复习提纲",
                  "根据 Chapter2-AssociationRule 整理复习提纲"):
        result = retriever.retrieve(query, top_k=6)
        assert result and {item.file_name for item in result} == {"Chapter2-AssociationRule.pdf"}

    assert retriever.retrieve("根据 Chapter9-AssociationRule 整理复习提纲") == []
    assert "没有找到已索引的 Chapter 9" in retriever.last_clarification
    assert retriever.retrieve("根据 Chapter 2 Network 整理复习提纲") == []
    assert "Chapter 2" in retriever.last_clarification
