"""Study requests must show the right PDF and representative pages to the LLM."""

from app.agent.answer_generator import LLMAnswerGenerator
from app.agent.factory import build_mock_orchestrator
from app.core.errors import RetrievalError
from app.core.types import AgentState, DocumentChunk
from app.retrieval.bm25_retriever import BM25Retriever
from app.retrieval.hybrid_retriever import HybridRetriever
from app.retrieval.study_intent import is_study_request


class OfflineEmbedding:
    def embed(self, text):
        raise RetrievalError("offline")


def make_chunk(name, page, text):
    return DocumentChunk(
        chunk_id=f"{name}:p{page}:c0", document_id=name,
        file_name=name, page_number=page, text=text,
    )


def corpus():
    chunks = [
        make_chunk("COMP7608B_Lec04.pdf", 1,
                   "PART II · LECTURE 1\nAI Agent: components and system design."),
        *[make_chunk("COMP7608B_Lec04.pdf", page,
                     f"Agent topic {page}: tools, planning, memory, and verification. " * 6)
          for page in range(2, 14)],
        make_chunk("COMP7608B_Lecture_06.pdf", 1,
                   "COMP7608B Lecture 6\nAgent skill and memory."),
        make_chunk("poster.pdf", 1,
                   "LECTURE 6\nLanguage / English\nDate / 19 Nov 2026"),
        *[make_chunk("rag_paper.pdf", page,
                     f"Research section {page}: retrieval augmented generation, "
                     "model training and experimental results. " * 5)
          for page in range(1, 11)],
        *[make_chunk("rag_paper.pdf", page,
                     "References\n" + f"Language model citation {page}. " * 5)
          for page in range(11, 15)],
    ]
    retriever = HybridRetriever(embedder=OfflineEmbedding())
    retriever._bm25 = BM25Retriever(chunks)
    retriever._bm25_loaded = True
    return retriever


def test_visible_lecture_number_overrides_different_internal_slide_number():
    retriever = corpus()
    for question in (
        "COMP7608B Lecture 04 里面都讲了什么",
        "comp7608_04 里 agent 都讲了些什么",
    ):
        result = retriever.retrieve(question, top_k=6)
        assert len(result) == 6
        assert {item.file_name for item in result} == {"COMP7608B_Lec04.pdf"}
        assert len({item.page_number for item in result}) == 6
        assert max(item.page_number for item in result) >= 10


def test_same_lecture_number_still_uses_structured_field_or_asks():
    retriever = corpus()
    result = retriever.retrieve("Lecture 6 的 Language 是什么")
    assert {item.file_name for item in result} == {"poster.pdf"}
    assert retriever.retrieve("Lecture 6 讲了什么") == []
    assert "poster.pdf" in retriever.last_clarification
    assert "COMP7608B_Lecture_06.pdf" in retriever.last_clarification


def test_comparison_has_both_documents_in_first_six_not_references():
    retriever = corpus()
    result = retriever.retrieve(
        "比较 COMP7608B_Lec04.pdf 和 rag_paper.pdf 的方法和结果", top_k=6
    )
    assert len(result) == 6
    assert [item.file_name for item in result[:2]] == [
        "COMP7608B_Lec04.pdf", "rag_paper.pdf",
    ]
    assert sum(item.file_name == "rag_paper.pdf" for item in result) == 3
    assert all(item.page_number <= 10 for item in result if item.file_name == "rag_paper.pdf")


def test_study_notes_span_pages_and_exact_fact_question_is_unchanged():
    retriever = corpus()
    summary = retriever.retrieve("根据 rag_paper.pdf 整理复习提纲", top_k=6)
    assert len({item.page_number for item in summary}) == 6
    assert max(item.page_number for item in summary) >= 9
    assert not is_study_request("rag_paper.pdf 第3页的 RAG-Token 是什么？")
    assert not is_study_request("rag_paper.pdf 第3页讲了什么？")
    assert not is_study_request("What does page 3 cover in rag_paper.pdf?")
    exact = retriever.retrieve("rag_paper.pdf 第3页的 RAG-Token 是什么？", top_k=2)
    assert len(exact) <= 2


def test_unspecified_this_pdf_asks_which_file_in_shared_library():
    retriever = corpus()
    assert retriever.retrieve("这篇PDF讲了什么？", top_k=6) == []
    assert "写出要总结或比较的文件名" in retriever.last_clarification


def test_orchestrator_gives_answer_model_both_sources_even_with_plan_steps():
    retriever = corpus()
    orch = build_mock_orchestrator(top_k=3)
    orch.retriever = retriever
    state = orch.run(
        "比较 COMP7608B_Lec04.pdf 和 rag_paper.pdf 的方法与结果",
        enable_routing=False, enable_memory=False, enable_verification=False,
    )
    assert {e.file_name for e in state.evidence[:6]} == {
        "COMP7608B_Lec04.pdf", "rag_paper.pdf"
    }
    assert len(state.evidence[:6]) == 6
    assert any(t.event == "study_coverage" for t in state.trace)


def test_answer_prompt_tells_model_to_compare_and_cite_both_sources():
    retriever = corpus()
    state = build_mock_orchestrator().run(
        "比较 COMP7608B_Lec04.pdf 和 rag_paper.pdf 的方法与结果",
        enable_routing=False, enable_retrieval=False,
        enable_memory=False, enable_verification=False,
    )
    state.evidence = retriever.retrieve(state.question, top_k=6)
    prompt = LLMAnswerGenerator(build_mock_orchestrator().llm)._grounded_prompt(state)
    assert "分别说明各文档的观点" in prompt
    assert "COMP7608B_Lec04.pdf" in prompt
    assert "rag_paper.pdf" in prompt
    assert "第 " in prompt


def test_answer_page_number_tracks_citation_source_not_citation_index():
    state = AgentState(question="总结 rag_paper.pdf 的结论")
    state.evidence = corpus().retrieve(state.question, top_k=6)
    assert state.evidence[-1].page_number != 6
    answer = LLMAnswerGenerator._align_cited_pages(
        state, "结论见第6页 [6]。另外第1页 [1][6]。网页资料第3页 [99] 未核实。"
    )
    assert f"第 {state.evidence[-1].page_number} 页 [6]" in answer
    assert f"第 1、{state.evidence[-1].page_number} 页 [1][6]" in answer
    assert "第3页 [99]" in answer
    assert any(t.event == "page_citation_correction" for t in state.trace)
