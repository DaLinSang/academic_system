"""网页搜索接进编排器的测试。

守的是"需要时真的去搜、不需要时不浪费"这条线，
以及搜索失败不能毁掉已有回答。
"""

from __future__ import annotations

from app.agent.factory import build_mock_orchestrator, build_orchestrator
from app.agent.web_search_policy import WebSearchPolicy
from app.core.mocks import (
    MockAnswerGenerator,
    MockMemoryManager,
    MockMemoryStore,
    MockPlanner,
    MockRetriever,
    MockVerifier,
)
from app.core.types import (
    Evidence,
    RetrievalMethod,
    RouteDecision,
    RouteType,
)
from app.routing.router import RuleBasedRouter


def web_evidence(n: int = 2) -> list[Evidence]:
    return [
        Evidence(
            chunk_id=f"web-{i}",
            document_id=f"https://site.test/{i}",
            file_name="site.test",
            page_number=0,
            text=f"网页正文 {i}",
            score=0.6,
            retrieval_method=RetrievalMethod.WEB,
            url=f"https://site.test/{i}",
            title=f"Result {i}",
        )
        for i in range(1, n + 1)
    ]


class FakeWebTool:
    def __init__(self, results=None, *, error: Exception | None = None) -> None:
        self.results = results if results is not None else web_evidence()
        self.error = error
        self.calls: list[str] = []
        self.last_warning: str | None = None

    def search(self, query: str, top_k: int = 5):
        self.calls.append(query)
        if self.error is not None:
            raise self.error
        return self.results[:top_k]


class WebRouter:
    """明确要求联网的路由。"""

    def route(self, question, has_documents=True, memory_context=None):
        return RouteDecision(
            route=RouteType.DIRECT,
            need_web_search=True,
            reason="测试用：明确要求联网",
        )

    @property
    def last_usage(self):
        return None


def build(tool=None, *, router=None, policy=None, enabled=True):
    return build_orchestrator(
        router=router or RuleBasedRouter(),
        planner=MockPlanner(),
        retriever=MockRetriever(),
        memory_store=MockMemoryStore(),
        memory_manager=MockMemoryManager(),
        verifier=MockVerifier(),
        answer_generator=MockAnswerGenerator(),
        llm=__import__("app.core.mocks", fromlist=["MockLLM"]).MockLLM(),
        web_search_tool=tool,
        web_search_policy=policy or WebSearchPolicy(),
        enable_web_search=enabled,
    )


def events(state, event):
    return [e for e in state.trace if e.stage == "web_search" and e.event == event]


# --------------------------------------------------------------------------- #
# 没工具 / 没必要：不联网
# --------------------------------------------------------------------------- #
def test_no_tool_means_no_web_stage():
    state = build(None).run("DeepSeek 最新版本是什么？", enable_memory=False)

    assert not events(state, "decision")
    assert state.final_answer.strip()


def test_mock_orchestrator_never_searches_web():
    state = build_mock_orchestrator().run("今天天气如何", enable_memory=False)

    assert not [e for e in state.trace if e.stage == "web_search"]


def test_unrelated_question_does_not_call_tool():
    tool = FakeWebTool()
    build(tool).run("讲义里 RAG 是什么？", enable_memory=False)

    assert tool.calls == []


def test_disabled_switch_prevents_search():
    tool = FakeWebTool()
    build(tool, router=WebRouter(), enabled=False).run("最新版本", enable_memory=False)

    assert tool.calls == []


# --------------------------------------------------------------------------- #
# 需要时真的去搜
# --------------------------------------------------------------------------- #
def test_router_request_triggers_search():
    tool = FakeWebTool()
    state = build(tool, router=WebRouter()).run("最新版本", enable_memory=False)

    assert tool.calls, "路由明确要求时应该联网"
    assert events(state, "decision")
    assert events(state, "end")


def test_web_evidence_is_added_to_state():
    tool = FakeWebTool(web_evidence(3))
    state = build(tool, router=WebRouter()).run("最新版本", enable_memory=False)

    web = [e for e in state.evidence if e.is_web]
    assert len(web) == 3
    assert all(e.url for e in web)


def test_decision_event_records_reason_and_trigger():
    state = build(FakeWebTool(), router=WebRouter()).run("最新版本", enable_memory=False)

    decision = events(state, "decision")[0]
    assert decision.detail["should_search"] is True
    assert decision.detail["trigger"] == "router_request"
    assert decision.detail["reason"]


def test_end_event_records_count_and_warning():
    tool = FakeWebTool()
    tool.last_warning = "正文抓取失败，使用摘要"
    state = build(tool, router=WebRouter()).run("最新版本", enable_memory=False)

    end = events(state, "end")[0]
    assert end.detail["count"] == 2
    assert end.detail["warning"] == "正文抓取失败，使用摘要"


def test_citations_include_web_evidence():
    state = build(FakeWebTool(), router=WebRouter()).run("最新版本", enable_memory=False)

    assert state.citations
    assert any("网页" in c.locator for c in state.citations)


# --------------------------------------------------------------------------- #
# 失败降级
# --------------------------------------------------------------------------- #
def test_tool_exception_does_not_break_answer():
    tool = FakeWebTool(error=RuntimeError("network down"))
    state = build(tool, router=WebRouter()).run("最新版本", enable_memory=False)

    assert state.final_answer.strip()
    assert any("web_search:" in e for e in state.errors)
    assert events(state, "error")


def test_failed_search_still_produces_serializable_state():
    tool = FakeWebTool(error=RuntimeError("down"))
    state = build(tool, router=WebRouter()).run("最新版本", enable_memory=False)

    assert state.model_dump_json()
