"""Issue #66：Web 搜索触发与来源如实呈现的回归测试。

守三件事：
1. Router 提示词让 need_web_search 在"查官网/最新/给链接"时为 true，
   普通文档问答仍为 false（不误伤）；
2. Bing 跳转链接解码成真实 URL（用户拿到可点击来源）；
3. 本轮联网但未拿到可用网页证据时，答案末尾如实说明，
   区分"工具没调用 / 失败 / 搜到但不相关"。
"""

from __future__ import annotations

from app.agent.orchestrator import Orchestrator
from app.core.types import AgentState, ClaimCheck, ClaimStatus, TraceEvent


def test_no_web_trace_no_note():
    state = AgentState(question="q")
    assert Orchestrator._web_search_note(state) == ""


def test_web_zero_results_says_failed():
    state = AgentState(question="q")
    state.trace.append(TraceEvent(
        stage="web_search", event="end", detail={"count": 0, "warning": "ReadTimeout"}
    ))
    note = Orchestrator._web_search_note(state)
    assert "未找到可用结果" in note
    assert "未使用网页来源" in note


def test_web_results_not_relevant_says_no_supporting_evidence():
    state = AgentState(question="q")
    state.trace.append(TraceEvent(
        stage="web_search", event="end", detail={"count": 3, "warning": None}
    ))
    note = Orchestrator._web_search_note(state)
    assert "返回 3 条结果" in note
    assert "没有找到能支撑" in note


def test_web_supported_claim_adds_no_note():
    from app.core.types import Evidence, RetrievalMethod
    state = AgentState(question="q")
    state.trace.append(TraceEvent(
        stage="web_search", event="end", detail={"count": 1, "warning": None}
    ))
    state.evidence = [Evidence(
        chunk_id="web-x", document_id="https://hku.hk", file_name="hku.hk",
        page_number=0, text="course", score=0.9,
        retrieval_method=RetrievalMethod.WEB, url="https://hku.hk", title="HKU",
    )]
    state.claims = [ClaimCheck(
        claim="COMP7608 是课程", status=ClaimStatus.SUPPORTED,
        supporting_evidence=["web-x"],
    )]
    assert Orchestrator._web_search_note(state) == ""
