"""Issue #89：搜索查询改写器测试。

守：
1. LLM 把口语问句改写成精简的核心实体词（实测精简 query 才能搜对）
2. 改写结果的清洗（引号/列表符/前缀/多行）
3. LLM 失败或空结果时回退原问题，不阻断联网
4. 用量可归集（RQ4）
"""

from __future__ import annotations

from app.agent.web_query_rewriter import WebQueryRewriter
from app.core.types import LLMResponse, Usage


class FakeLLM:
    def __init__(self, text="计算机科学与技术", *, error=None):
        self.text = text
        self.error = error
        self.calls = 0
        self.prompts: list[str] = []

    def generate(self, messages, **kwargs):
        self.calls += 1
        self.prompts.append(messages[0]["content"])
        if self.error:
            raise self.error
        return LLMResponse(text=self.text, usage=Usage(total_tokens=30), model="fake")


def test_rewrite_core_entity():
    llm = FakeLLM("计算机科学与技术")
    rw = WebQueryRewriter(llm)
    assert rw.rewrite("你上网帮我搜一下计算机科学专业怎么样") == "计算机科学与技术"
    assert llm.calls == 1
    assert rw.last_usage.total_tokens == 30


def test_prompt_requires_concise_terms():
    llm = FakeLLM()
    WebQueryRewriter(llm).rewrite("香港天气怎么样")
    prompt = llm.prompts[0]
    assert "2-4 个词" in prompt
    assert "计算机科学与技术" in prompt  # 完整术语示例
    assert "越简洁越好" in prompt


def test_clean_quotes_and_prefix():
    for raw, want in [
        ('"香港天气"', "香港天气"),
        ("- 香港天气", "香港天气"),
        ("查询：香港天气", "香港天气"),
        ("香港天气\n解释：略", "香港天气"),
    ]:
        assert WebQueryRewriter._clean(raw) == want, f"{raw!r} 清洗失败"


def test_empty_result_falls_back():
    rw = WebQueryRewriter(FakeLLM("   "))
    assert rw.rewrite("原始问题") == "原始问题"


def test_llm_error_falls_back():
    rw = WebQueryRewriter(FakeLLM("", error=RuntimeError("boom")))
    assert rw.rewrite("原始问题") == "原始问题"


def test_empty_question_returns_as_is():
    rw = WebQueryRewriter(FakeLLM())
    assert rw.rewrite("") == ""


# --------------------------------------------------------------------------- #
# Orchestrator 接线：改写后的 query 真的传给搜索工具
# --------------------------------------------------------------------------- #
def test_orchestrator_uses_rewritten_query():
    from app.agent.factory import build_orchestrator
    from app.agent.orchestrator import MODE_PRESETS
    from app.core.mocks import (
        MockAnswerGenerator,
        MockLLM,
        MockMemoryManager,
        MockMemoryStore,
        MockPlanner,
        MockRetriever,
        MockVerifier,
    )
    from app.core.types import RouteDecision, RouteType

    class WebRouter:
        last_usage = None
        def route(self, question, has_documents=True, memory_context=None, history=None):
            return RouteDecision(route=RouteType.DIRECT, need_retrieval=False,
                                 need_web_search=True, reason="test")

    seen = {}

    class RecordingSearch:
        last_warning = None
        last_diagnostics = []
        def search(self, query, top_k=5):
            seen["query"] = query
            return []

    orch = build_orchestrator(
        router=WebRouter(), planner=MockPlanner(), retriever=MockRetriever(),
        memory_store=MockMemoryStore(), memory_manager=MockMemoryManager(),
        verifier=MockVerifier(), answer_generator=MockAnswerGenerator(), llm=MockLLM(),
        web_search_tool=RecordingSearch(), enable_web_search=True,
        web_query_rewriter=WebQueryRewriter(FakeLLM("计算机科学与技术")),
    )
    orch.run("你上网帮我搜一下计算机科学专业怎么样",
             **MODE_PRESETS["B4"])
    assert seen["query"] == "计算机科学与技术"


def test_without_rewriter_uses_original_query():
    """没注入 rewriter 时行为与以前完全一致（向后兼容）。"""
    from app.agent.factory import build_orchestrator
    from app.agent.orchestrator import MODE_PRESETS
    from app.core.mocks import (
        MockAnswerGenerator,
        MockLLM,
        MockMemoryManager,
        MockMemoryStore,
        MockPlanner,
        MockRetriever,
        MockVerifier,
    )
    from app.core.types import RouteDecision, RouteType

    class WebRouter:
        last_usage = None
        def route(self, question, has_documents=True, memory_context=None, history=None):
            return RouteDecision(route=RouteType.DIRECT, need_retrieval=False,
                                 need_web_search=True, reason="test")

    seen = {}
    class RecordingSearch:
        last_warning = None
        last_diagnostics = []
        def search(self, query, top_k=5):
            seen["query"] = query
            return []

    orch = build_orchestrator(
        router=WebRouter(), planner=MockPlanner(), retriever=MockRetriever(),
        memory_store=MockMemoryStore(), memory_manager=MockMemoryManager(),
        verifier=MockVerifier(), answer_generator=MockAnswerGenerator(), llm=MockLLM(),
        web_search_tool=RecordingSearch(), enable_web_search=True,
    )
    orch.run("你上网帮我搜一下计算机科学专业怎么样", **MODE_PRESETS["B4"])
    assert "计算机科学" in seen["query"]  # 原文
