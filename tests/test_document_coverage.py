from app.core.errors import RetrievalError
from app.core.types import DocumentChunk
from app.retrieval.bm25_retriever import BM25Retriever
from app.retrieval.hybrid_retriever import HybridRetriever


class OfflineEmbedder:
    def embed(self, text):
        raise RetrievalError("offline")


def corpus():
    chunks = [DocumentChunk(chunk_id=f"paper:{i}", document_id="paper",
             file_name="paper.pdf", page_number=i, text=text)
              for i, text in enumerate([
                  "Abstract introduction motivation objective research goal",
                  "method approach neural retrieval architecture",
                  "experiment results dataset accuracy improvement",
                  "conclusion discussion limitation future work"], 1)]
    chunks.append(DocumentChunk(chunk_id="other", document_id="other",
                  file_name="other.pdf", page_number=1, text="abstract method conclusion"))
    retriever = HybridRetriever(embedder=OfflineEmbedder())
    retriever._bm25 = BM25Retriever(chunks)
    retriever._bm25_loaded = True
    return retriever


def test_overview_covers_sections_even_when_embedding_is_offline():
    retriever = corpus()
    result = retriever.retrieve("总结 paper.pdf 研究目标、方法和主要结论", top_k=6)
    assert {r.page_number for r in result} == {1, 2, 3, 4}
    assert {r.file_name for r in result} == {"paper.pdf"}
    assert retriever.last_warning


def test_unknown_document_cannot_silently_search_another_pdf():
    assert corpus().retrieve("summarize missing.pdf") == []


def test_scoped_search_obeys_budget():
    assert len(corpus().retrieve("paper.pdf method", top_k=1)) == 1


def test_followup_retrieval_receives_previous_user_question():
    from app.agent.factory import build_mock_orchestrator
    from app.core.types import ConversationTurn
    orch = build_mock_orchestrator()
    queries = []
    orch.retriever.retrieve = lambda query, top_k: queries.append(query) or []
    orch.run('这篇论文中它的方法是什么？', history=[ConversationTurn(
        question='请介绍 paper.pdf 的 RAG 方法', answer='不可信的答案不应加入查询', turn_index=0
    )])
    assert queries and 'paper.pdf' in queries[0]
    assert '不可信的答案' not in queries[0]


def test_real_evaluation_rejects_missing_key(monkeypatch, tmp_path):
    import pytest

    from app.evaluation import batch_runner
    monkeypatch.setattr(type(batch_runner.settings.llm), 'resolve', lambda self: ('', '', ''))
    with pytest.raises(ValueError, match='API key'):
        batch_runner.run_evaluation(samples=[{}], modes=['B4'], real=True, runs_dir=tmp_path)
