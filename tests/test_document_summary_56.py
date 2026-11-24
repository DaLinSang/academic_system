"""方案 §3/§7/§8.1：A 的 PDF 总结编排与稳定 Web 后端测试。

守：
1. 意图识别：summary/qa/comparison 分类，指定文件，普通问答不误触发
2. 大文档按页分段（不切断页、不超长）
3. 小文档一次性总结；大文档分层总结（每段+合并），usage 累加
4. 不编造页码、只输出正文（prompt 约束）
5. Factory 默认只启用 Tavily，无 key 不启用 HTML
"""

from __future__ import annotations

from app.agent.document_intent import document_task, requested_document, should_summarize
from app.agent.document_summary import split_pages_into_segments, summarize_document
from app.core.types import AgentState, LLMResponse, Usage


# --------------------------------------------------------------------------- #
# 1. 意图识别
# --------------------------------------------------------------------------- #
def test_intent_summary_with_named_file():
    q = "总结 rag_paper.pdf 的研究问题、方法、结果和局限"
    assert document_task(q) == "summary"
    assert requested_document(q, ["rag_paper.pdf"]) == "rag_paper.pdf"
    assert should_summarize(q, ["rag_paper.pdf"])


def test_intent_qa_not_summarized():
    # 方案 §3.5：这类普通 RAG 问答不应走全文总结
    assert document_task("RAG 有什么优势") == "qa"
    assert not should_summarize("RAG 有什么优势", ["rag_paper.pdf"])
    assert not should_summarize("rag_paper.pdf 用了什么方法", ["rag_paper.pdf"])


def test_intent_comparison_not_summarized():
    q = "比较 A.pdf 和 B.pdf 的方法"
    assert document_task(q) == "comparison"
    # 多份文件无法唯一确定 → AMBIGUOUS；comparison 不走单篇总结
    from app.agent.document_intent import AMBIGUOUS
    assert requested_document(q, ["A.pdf", "B.pdf"]) == AMBIGUOUS
    assert not should_summarize(q, ["A.pdf", "B.pdf"])


# --------------------------------------------------------------------------- #
# 方案 §4.3/§4.4：当前会话文档与"这个文档"指代
# --------------------------------------------------------------------------- #
def test_deictic_uses_active_document():
    from app.agent.document_intent import requested_document
    q = "请总结这个文档"
    assert requested_document(q, ["a.pdf", "b.pdf", "c.pdf"], "rag_paper.pdf") == "rag_paper.pdf"
    assert should_summarize(q, ["a.pdf", "b.pdf"], "rag_paper.pdf")


def test_deictic_ambiguous_without_active_and_many_files():
    from app.agent.document_intent import AMBIGUOUS, requested_document
    assert requested_document("总结这个文档", ["a.pdf", "b.pdf"], None) == AMBIGUOUS
    assert not should_summarize("总结这个文档", ["a.pdf", "b.pdf"], None)


def test_explicit_filename_beats_active_document():
    from app.agent.document_intent import requested_document
    assert requested_document("总结 rag_paper.pdf", ["rag_paper.pdf", "x.pdf"], "x.pdf") == "rag_paper.pdf"


def test_agentstate_has_active_document_field():
    from app.core.types import AgentState
    s = AgentState(question="q")
    assert s.active_document_file_name is None
    s.active_document_file_name = "rag_paper.pdf"
    assert s.active_document_file_name == "rag_paper.pdf"


def test_intent_single_indexed_file_inferred():
    assert should_summarize("这篇论文主要讲了什么", ["rag_paper.pdf"])


def test_intent_revision_outline_counts_as_summary():
    assert should_summarize("请分析 Chapter9.pdf 并整理复习提纲", ["Chapter9.pdf"])


# --------------------------------------------------------------------------- #
# 2. 分段
# --------------------------------------------------------------------------- #
def test_split_pages_preserves_page_boundaries():
    text = "\n\n".join(f"[Page {i}]\n第{i}页内容abc" for i in range(1, 6))
    segs = split_pages_into_segments(text, max_chars=40)
    assert len(segs) > 1
    # 每页必须完整落在某一段，且页标签保留
    joined = "\n\n".join(segs)
    for i in range(1, 6):
        assert f"[Page {i}]" in joined
        assert f"第{i}页内容abc" in joined


def test_split_small_text_single_segment():
    assert len(split_pages_into_segments("[Page 1]\n短文本", max_chars=40000)) == 1


# --------------------------------------------------------------------------- #
# 3. 总结路径与 usage
# --------------------------------------------------------------------------- #
class FakeLLM:
    def __init__(self):
        self.prompts = []

    def generate(self, messages, **kwargs):
        self.prompts.append(messages)
        return LLMResponse(
            text="这是总结正文。",
            usage=Usage(total_tokens=10, llm_calls=1),
            model="fake",
        )


def test_small_document_one_call():
    llm = FakeLLM()
    state = AgentState(question="总结文档", document_text="[Page 1]\n短文档", document_file_name="a.pdf")
    out = summarize_document(llm, state)
    assert out == "这是总结正文。"
    assert len(llm.prompts) == 1
    assert state.usage.llm_calls == 1
    assert any(e.event == "mode" and e.detail["mode"] == "full" for e in state.trace)


def test_large_document_hierarchical_calls_accumulate():
    llm = FakeLLM()
    # 3 页，每页 > segment_chars，强制三段
    pages = "\n\n".join(f"[Page {i}]\n{'x' * 60}" for i in range(1, 4))
    state = AgentState(question="总结文档", document_text=pages, document_file_name="a.pdf")
    summarize_document(llm, state, small_doc_chars=10, segment_chars=40)
    # 3 段摘要 + 1 次合并 = 4 次调用
    assert len(llm.prompts) == 4
    assert state.usage.llm_calls == 4
    assert state.usage.total_tokens == 40
    assert any(e.event == "mode" and e.detail["mode"] == "hierarchical" for e in state.trace)


def test_summary_prompt_forbids_source_list():
    llm = FakeLLM()
    state = AgentState(question="总结文档", document_text="[Page 1]\n短文档")
    summarize_document(llm, state)
    system = llm.prompts[0][0]["content"]
    assert "不要输出来源列表" in system
    assert "不要编造页码" in system or "无法定位时不要编造页码" in system


# --------------------------------------------------------------------------- #
# C review #1/#2/#3：逐条 scoped 检索 + unverified，不用全库过滤冒充
# --------------------------------------------------------------------------- #
def test_per_claim_scoped_retrieval_and_unverified():
    """超过预算的 Claim 必须标 unverified，不能 unsupported；证据只来自目标 PDF。"""
    from app.agent.orchestrator import Orchestrator
    from app.core.types import AgentState, ClaimStatus, Evidence, RetrievalMethod

    class FakeScopedRetriever:
        def retrieve_for_claim(self, claim, file_names, top_k=3):
            assert file_names == ["target.pdf"], "必须按目标文件 scoped 检索"
            return [Evidence(
                chunk_id=f"c-{claim[:6]}", document_id="d", file_name="target.pdf",
                page_number=1, text="evidence", score=0.9,
                retrieval_method=RetrievalMethod.RRF,
            )]

    class FakeVerifier:
        last_usage = None
        def verify(self, answer, evidence):
            from app.core.types import ClaimCheck
            return [ClaimCheck(claim=answer, status=ClaimStatus.SUPPORTED,
                               supporting_evidence=[e.chunk_id for e in evidence])]

    orch = Orchestrator(
        llm=None, router=None, planner=None, retriever=FakeScopedRetriever(),
        memory_store=None, memory_manager=None, verifier=FakeVerifier(),
        answer_generator=None,
    )
    # 造 12 条可被 split_claims 识别的事实句
    answer = "\n".join(f"第{i}条陈述包含足够长度的中文事实内容用于拆分。" for i in range(12))
    state = AgentState(question="总结 target.pdf", document_text="全文", document_file_name="target.pdf")
    state.draft_answer = answer

    done = orch._supplement_evidence_for_summary(state)
    assert done is True
    assert len(state.claims) == 12
    verified = [c for c in state.claims if c.status != ClaimStatus.UNVERIFIED]
    unverified = [c for c in state.claims if c.status == ClaimStatus.UNVERIFIED]
    assert len(verified) == orch.MAX_VERIFIED_CLAIMS
    assert len(unverified) == 12 - orch.MAX_VERIFIED_CLAIMS
    assert all(c.note for c in unverified), "unverified 必须带原因"
    assert all(
        e.file_name == "target.pdf" for e in state.evidence
    ), "证据必须全部来自目标 PDF"
    assert any(e.stage == "post_hoc_retrieval" and e.event == "end" for e in state.trace)


def test_no_scoped_retriever_degrades_without_fake_evidence():
    """没有 retrieve_for_claim 时不做全库检索兜底，直接降级 unverified。"""
    from app.agent.orchestrator import Orchestrator
    from app.core.types import AgentState

    class NoScopedRetriever:
        def retrieve(self, query, top_k=5):
            raise AssertionError("C review #3：不能拿全库检索冒充 scoped")

    orch = Orchestrator(
        llm=None, router=None, planner=None, retriever=NoScopedRetriever(),
        memory_store=None, memory_manager=None, verifier=None, answer_generator=None,
    )
    state = AgentState(question="总结 x.pdf", document_text="全文", document_file_name="x.pdf")
    state.draft_answer = "这是一条足够长的中文事实陈述内容用于拆分。"
    assert orch._supplement_evidence_for_summary(state) is False
    assert any(e.stage == "post_hoc_retrieval" and e.detail.get("reason") == "no_scoped_retriever"
               for e in state.trace)


def test_colloquial_teach_me_about_this_article():
    """用户原话边界：'给我讲讲这篇文章' 必须识别为通读全文总结。"""
    from app.agent.document_intent import (
        AMBIGUOUS,
        document_task,
        requested_document,
        should_summarize,
    )
    many = ["rag_paper.pdf", "comp7608_lecture_agent.pdf"]
    for q in ["给我讲讲这篇文章", "讲讲这篇文章", "介绍一下这个文档", "这篇文章讲了什么"]:
        assert document_task(q) == "summary", q
        assert requested_document(q, many, None) == AMBIGUOUS, q
        assert requested_document(q, many, "rag_paper.pdf") == "rag_paper.pdf", q
        assert should_summarize(q, many, "rag_paper.pdf"), q
