"""Issue #64：规划子查询继承来源约束与多轮指代。

守三件事：
1. Planner 拆出的子查询丢失原问题文件名时，自动补回（来源限定生效）；
2. 短指代追问补最近用户问题，不把助手回答当证据；
3. retrieval/query trace 记录实际查询与来源约束，UI/报告可核对。
"""

from __future__ import annotations

from app.agent.orchestrator import Orchestrator
from app.core.types import AgentState, ConversationTurn


def state_with(question, history=None, plan=None):
    s = AgentState(question=question, history=history or [])
    if plan is not None:
        s.plan = plan
    return s


def test_planner_query_inherits_pdf_filename():
    """Planner 把 'rag_paper.pdf' 丢了，子查询要补回来。"""
    s = state_with(
        "请总结 rag_paper.pdf 的研究目标、方法和结论",
        plan=[],  # 只为占位，本测试直接调 _enrich_query
    )
    enriched = Orchestrator._enrich_query("研究目标 问题定义 研究动机", s)
    assert "rag_paper.pdf" in enriched
    assert "研究目标" in enriched


def test_enrich_does_not_duplicate_filename():
    """子查询已带文件名时不重复注入。"""
    s = state_with("请总结 rag_paper.pdf 的内容")
    enriched = Orchestrator._enrich_query("rag_paper.pdf 方法 实验", s)
    assert enriched.count("rag_paper.pdf") == 1


def test_enrich_uses_history_question_not_answer():
    """短指代补的是历史用户问题，不把助手回答当事实。"""
    s = state_with(
        "它主要解决什么问题？",
        history=[
            ConversationTurn(
                question="我在读 COMP7608B_Lec04.pdf 这篇论文",
                answer="这是助手编的事实：RAG 解决了全部问题",  # 不能被注入
                turn_index=0,
            )
        ],
    )
    enriched = Orchestrator._enrich_query("它的方法", s)
    assert "COMP7608B_Lec04.pdf" in enriched
    assert "助手编的事实" not in enriched
    assert "当前问题：它的方法" in enriched


def test_enrich_no_history_no_change():
    """无历史、无文件名时查询原样返回。"""
    s = state_with("什么是 attention")
    assert Orchestrator._enrich_query("什么是 attention", s) == "什么是 attention"


def test_no_retrieval_query_event_without_run():
    """_enrich_query 是纯函数，不产生 trace（守卫无副作用）。"""
    s = state_with("请总结 rag_paper.pdf", history=[])
    Orchestrator._enrich_query("方法", s)
    assert s.trace == []
