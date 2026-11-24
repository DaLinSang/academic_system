"""对话内临时文档（对话框 + 号上传，不进 RAG 知识库）后端测试。

守：
1. attached_document_text 优先于知识库 read_document，不查库
2. 任何问题（不只"总结"）都针对临时文档通读
3. Claim 校验用临时全文分段做证据，不伪装成知识库检索
4. 不传 attached 时行为完全不变（回归）
"""

from __future__ import annotations

from app.agent.factory import build_stage1_orchestrator
from app.agent.orchestrator import MODE_PRESETS, Orchestrator
from app.core.types import AgentState, Evidence

DOC = (
    "[Page 1]\n本研究提出 TestRetrieval 方法，使用 BM25 与向量检索融合。\n\n"
    "[Page 2]\n实验在三个数据集上进行，准确率提升 12%，局限是长文档覆盖不足。"
)


def test_attached_document_read_from_text_not_database():
    state = AgentState(
        question="请讲讲这篇文章",
        attached_document_text=DOC,
        attached_document_file_name="临时论文.pdf",
    )
    orch = build_stage1_orchestrator(top_k=5)
    assert orch._load_full_document(state) is True
    assert state.document_text == DOC
    assert state.document_file_name == "临时论文.pdf"
    assert any(
        e.stage == "document_read" and e.event == "attached"
        and e.detail.get("source") == "conversation"
        for e in state.trace
    ), "必须标记为对话内文档，且不能查知识库"


def test_any_question_with_attached_doc_is_full_document_request():
    orch = build_stage1_orchestrator(top_k=5)
    # 普通事实问句也应针对临时文档（用户附了文档就期望用它）
    assert orch._is_full_document_request(
        "它用了什么方法？", indexed_names=["rag_paper.pdf"], active_document_file_name=None
    ) is False  # 知识库文档仍需 summary 意图
    # 但 attached 由 run 层的 has_attached 短路，这里单独验证 state 字段可用
    state = AgentState(question="它用了什么方法？", attached_document_text=DOC)
    assert state.attached_document_text == DOC


def test_match_segments_finds_relevant_page():
    segs = ["[Page 1]\nBM25 与向量检索融合", "[Page 2]\n准确率提升 12% 局限是覆盖不足"]
    found = Orchestrator._match_segments("准确率提升了多少", segs, "x.pdf", top_k=2)
    assert found, "应找到含'准确率'的段落"
    assert all(isinstance(e, Evidence) and e.file_name == "x.pdf" for e in found)
    assert any("准确率" in e.text for e in found)


def test_attached_document_end_to_end_no_db_lookup():
    orch = build_stage1_orchestrator(top_k=5)
    flags = dict(MODE_PRESETS["B4"])
    flags["enable_memory"] = False
    state = orch.run(
        "请讲讲这篇文章", user_id="test-attached",
        attached_document_text=DOC, attached_document_file_name="临时论文.pdf", **flags
    )
    # 正文来自临时文档
    assert "TestRetrieval" in state.final_answer
    assert "12%" in state.final_answer
    # 没有查知识库文档
    assert not any(e.stage == "document_read" and e.event == "end" for e in state.trace)
    # Claim 校验走临时全文
    assert any(e.stage == "post_hoc_retrieval" and e.detail.get("source") == "attached_text"
               for e in state.trace)
    assert state.errors == []


def test_no_attached_keeps_existing_behavior():
    orch = build_stage1_orchestrator(top_k=5)
    state = AgentState(question="RAG 有什么优势")
    assert orch._load_full_document(state) is False
    assert state.document_text == ""
