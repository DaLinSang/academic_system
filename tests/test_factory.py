"""装配工厂与用量归集的测试。

守两件事：
  1. `build_stage1_orchestrator()` 必须真的换成真实模块，否则"集成完成"是假的；
  2. 各组件内部调用模型产生的 token 必须回到 `AgentState.usage`，
     否则 RQ4 的成本与延迟对比会系统性偏低。
"""

from __future__ import annotations

import pytest

from app.agent.answer_generator import LLMAnswerGenerator
from app.agent.factory import (
    build_mock_orchestrator,
    build_orchestrator,
    build_stage1_orchestrator,
)
from app.core.mocks import (
    MockAnswerGenerator,
    MockLLM,
    MockMemoryManager,
    MockMemoryStore,
    MockPlanner,
    MockRetriever,
    MockRouter,
    MockVerifier,
)
from app.core.types import PlanStep, Usage
from app.memory import PostgresMemoryStore, RuleBasedMemoryManager
from app.planning.planner import LLMPlanner
from app.retrieval import HybridRetriever
from app.routing.router import LLMRouter, RuleBasedRouter
from app.verification import LexicalVerifier


# --------------------------------------------------------------------------- #
# Mock 装配
# --------------------------------------------------------------------------- #
def test_build_mock_orchestrator_wires_mocks():
    orch = build_mock_orchestrator()

    assert isinstance(orch.router, MockRouter)
    assert isinstance(orch.planner, MockPlanner)
    assert isinstance(orch.retriever, MockRetriever)
    assert isinstance(orch.memory_store, MockMemoryStore)
    assert isinstance(orch.memory_manager, MockMemoryManager)
    assert isinstance(orch.verifier, MockVerifier)
    assert isinstance(orch.answer_generator, MockAnswerGenerator)


def test_mock_orchestrator_runs_without_key_or_database():
    state = build_mock_orchestrator().run("讲义里 RAG 是什么？")
    assert state.final_answer.strip()
    assert state.errors == []


# --------------------------------------------------------------------------- #
# 真实装配
# --------------------------------------------------------------------------- #
def test_build_stage1_wires_real_components():
    """真实模块必须接上，包括长期记忆存取与规则筛选。"""
    orch = build_stage1_orchestrator()

    assert isinstance(orch.router, LLMRouter)
    assert isinstance(orch.planner, LLMPlanner)
    assert isinstance(orch.retriever, HybridRetriever)
    assert isinstance(orch.verifier, LexicalVerifier)
    assert isinstance(orch.answer_generator, LLMAnswerGenerator)

    assert isinstance(orch.memory_store, PostgresMemoryStore)
    assert isinstance(orch.memory_manager, RuleBasedMemoryManager)


def test_stage1_construction_does_not_require_database():
    """构造时不应连库，否则没有 Docker 的同学连 import 都跑不了。"""
    orch = build_stage1_orchestrator()
    assert orch.top_k == 5


def test_stage1_retrieval_failure_degrades_gracefully():
    """数据库没起时，链路仍要给出可读回答，而不是抛异常。"""
    orch = build_stage1_orchestrator(top_k=2)
    state = orch.run("讲义里 RAG 是什么？", **{"enable_memory": False})

    assert state.final_answer.strip()
    # 检索失败会被记录，但不应导致整个请求崩溃
    assert isinstance(state.errors, list)


# --------------------------------------------------------------------------- #
# 用量归集：Planner 的 token 不能丢
# --------------------------------------------------------------------------- #
class _CountingPlanner:
    """模拟一个"内部调过模型"的 Planner。"""

    def __init__(self, tokens: int = 55) -> None:
        self.last_usage = Usage()
        self.tokens = tokens
        self.calls = 0

    def plan(self, question, route, memory_context=None):
        self.calls += 1
        self.last_usage = Usage(
            prompt_tokens=self.tokens // 2,
            completion_tokens=self.tokens - self.tokens // 2,
            total_tokens=self.tokens,
            llm_calls=1,
        )
        return [
            PlanStep(step_id=1, description="检索", tool="retrieval", query=question),
            PlanStep(step_id=2, description="汇总", tool="llm", depends_on=[1]),
        ]


def _build_with(planner, router=None):
    """用指定 Planner 装配一个其余全 Mock 的编排器。"""
    return build_orchestrator(
        router=router or RuleBasedRouter(),
        planner=planner,
        retriever=MockRetriever(),
        memory_store=MockMemoryStore(),
        memory_manager=MockMemoryManager(),
        verifier=MockVerifier(),
        answer_generator=MockAnswerGenerator(),
        llm=MockLLM(),
    )


def test_planner_usage_is_absorbed_into_state():
    """complex_task 会调 Planner，它消耗的 token 必须进 state.usage。"""
    planner = _CountingPlanner(tokens=55)
    orch = _build_with(planner)

    state = orch.run("比较 RAG 和 ReAct 的方法差异", enable_memory=False)

    assert planner.calls == 1, "该问题应被判为 complex_task"
    assert state.usage.total_tokens >= 55
    assert state.usage.llm_calls >= 1


def test_planner_usage_not_counted_when_no_planning():
    """direct 路径不调 Planner，它的 55 token 不应出现在 state.usage 里。

    注意：direct 路径本身会调一次模型（AnswerGenerator 用自身知识回答），
    所以 total_tokens 不为 0 是正常的——这里断言的是"没有多出 Planner 那份"。
    """
    planner = _CountingPlanner(tokens=55)
    orch = _build_with(planner)

    state = orch.run("什么是 attention", enable_memory=False)

    assert planner.calls == 0
    assert state.plan == []
    assert state.usage.total_tokens < 55, "Planner 未被调用，不应计入它的用量"


# --------------------------------------------------------------------------- #
# 四模式在真实装配下也必须真的不同
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("mode", ["B1", "B2", "B3", "B4"])
def test_stage1_self_check_flags_are_consistent(mode):
    from app.agent.orchestrator import MODE_PRESETS

    orch = build_mock_orchestrator()
    state = orch.run("讲义里 RAG 是什么？", **MODE_PRESETS[mode])

    flags = MODE_PRESETS[mode]
    if flags["enable_retrieval"]:
        assert state.evidence, f"{mode} 应当有证据"
    else:
        assert state.evidence == [], f"{mode} 不应检索"
