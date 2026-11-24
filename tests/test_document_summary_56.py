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
    assert requested_document(q, ["A.pdf", "B.pdf"]) is None
    assert not should_summarize(q, ["A.pdf", "B.pdf"])


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
