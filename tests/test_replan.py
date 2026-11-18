"""校验后重规划的测试（Issue #47）。

守三条：
1. 只在有 unsupported claim 且本来就有计划时才重规划；
2. 预算硬上限 1 次，不让"反复修复"污染 RQ4 成本统计；
3. 重规划失败不能毁掉已有回答。
"""

from __future__ import annotations

from app.agent.factory import build_orchestrator
from app.core.mocks import (
    MockAnswerGenerator,
    MockLLM,
    MockMemoryManager,
    MockMemoryStore,
    MockRetriever,
)
from app.core.types import ClaimCheck, ClaimStatus, PlanStep, RouteType
from app.routing.router import RuleBasedRouter


class FlakyVerifier:
    """第一次返回 unsupported，之后返回 supported。"""

    def __init__(self) -> None:
        self.calls = 0

    def verify(self, answer, evidence):
        self.calls += 1
        status = ClaimStatus.UNSUPPORTED if self.calls == 1 else ClaimStatus.SUPPORTED
        return [ClaimCheck(claim="RAG 减少幻觉", status=status, confidence=0.7)]


class AlwaysUnsupportedVerifier:
    def __init__(self) -> None:
        self.calls = 0

    def verify(self, answer, evidence):
        self.calls += 1
        return [ClaimCheck(claim="未证实", status=ClaimStatus.UNSUPPORTED)]


class CountingPlanner:
    """每次返回不同检索词，用来验证真的用了新计划。"""

    def __init__(self) -> None:
        self.calls = 0

    def plan(self, question, route, memory_context=None):
        self.calls += 1
        query = "RAG" if self.calls == 1 else "ReAct"
        return [
            PlanStep(step_id=self.calls, description=f"检索 {query}", tool="retrieval", query=query),
            PlanStep(step_id=100 + self.calls, description="分析", tool="llm"),
        ]


def build(verifier, planner, *, enable_replan=True, max_replans=1):
    return build_orchestrator(
        router=RuleBasedRouter(),
        planner=planner,
        retriever=MockRetriever(),
        memory_store=MockMemoryStore(),
        memory_manager=MockMemoryManager(),
        verifier=verifier,
        answer_generator=MockAnswerGenerator(),
        llm=MockLLM(),
        enable_replan=enable_replan,
        max_replans=max_replans,
    )


COMPLEX = "比较 RAG 和 ReAct 的差异"


def _events(state, stage, event):
    return [e for e in state.trace if e.stage == stage and e.event == event]


# --------------------------------------------------------------------------- #
# 正常触发
# --------------------------------------------------------------------------- #
def test_replan_runs_when_unsupported_claim_found():
    verifier, planner = FlakyVerifier(), CountingPlanner()
    state = build(verifier, planner).run(COMPLEX, enable_memory=False)

    assert state.route is not None and state.route.route is RouteType.COMPLEX_TASK
    assert _events(state, "replan", "start")
    assert _events(state, "replan", "end")
    assert planner.calls == 2, "应该规划两次"
    assert verifier.calls == 2, "应该重新校验一次"


def test_replan_uses_new_plan_queries():
    planner = CountingPlanner()
    state = build(FlakyVerifier(), planner).run(COMPLEX, enable_memory=False)

    queries = [r.metadata["query"] for r in state.tool_results if r.metadata.get("query")]
    assert "RAG" in queries
    assert "ReAct" in queries, "重规划的新查询应该真的执行了"


def test_replan_budget_is_consumed():
    orch = build(FlakyVerifier(), CountingPlanner())
    state = orch.run(COMPLEX, enable_memory=False)

    assert orch.max_replans == 0
    assert _events(state, "replan", "end")[0].detail["remaining_unsupported"] == 0


def test_unsupported_claims_resolve_after_replan():
    state = build(FlakyVerifier(), CountingPlanner()).run(COMPLEX, enable_memory=False)
    assert state.unsupported_claims == []


# --------------------------------------------------------------------------- #
# 不该触发的场景
# --------------------------------------------------------------------------- #
def test_replan_can_be_disabled():
    verifier, planner = FlakyVerifier(), CountingPlanner()
    state = build(verifier, planner, enable_replan=False).run(COMPLEX, enable_memory=False)

    assert planner.calls == 1
    assert not _events(state, "replan", "start")
    assert len(state.unsupported_claims) == 1


def test_direct_route_does_not_replan():
    verifier, planner = FlakyVerifier(), CountingPlanner()
    state = build(verifier, planner).run("什么是 attention", enable_memory=False)

    assert state.route is not None and state.route.is_direct
    assert planner.calls == 0, "direct 路径没有计划，不需要重规划"


def test_no_unsupported_claim_means_no_replan():
    class AllSupported:
        def verify(self, answer, evidence):
            return [ClaimCheck(claim="ok", status=ClaimStatus.SUPPORTED)]

    planner = CountingPlanner()
    state = build(AllSupported(), planner).run(COMPLEX, enable_memory=False)

    assert planner.calls == 1
    assert not _events(state, "replan", "start")


def test_zero_budget_logs_skip():
    planner = CountingPlanner()
    state = build(
        AlwaysUnsupportedVerifier(), planner, max_replans=0
    ).run(COMPLEX, enable_memory=False)

    assert planner.calls == 1
    skips = _events(state, "replan", "skip")
    assert skips and skips[0].detail["reason"] == "budget_exhausted"


def test_only_one_replan_even_if_still_unsupported():
    """预算锁死：即使重规划后仍有 unsupported，也不再继续。"""
    verifier, planner = AlwaysUnsupportedVerifier(), CountingPlanner()
    state = build(verifier, planner).run(COMPLEX, enable_memory=False)

    assert planner.calls == 2
    assert verifier.calls == 2
    assert len(state.unsupported_claims) == 1, "保留真实结果，不假装修好"


# --------------------------------------------------------------------------- #
# 失败与记录
# --------------------------------------------------------------------------- #
def test_planner_failure_does_not_break_answer():
    class BrokenPlanner:
        def __init__(self) -> None:
            self.calls = 0

        def plan(self, question, route, memory_context=None):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("planner down")
            return [PlanStep(step_id=1, description="检索", tool="retrieval", query="RAG")]

    planner = BrokenPlanner()
    state = build(AlwaysUnsupportedVerifier(), planner).run(COMPLEX, enable_memory=False)

    assert state.final_answer.strip()
    assert any("replan:" in e for e in state.errors)
    assert _events(state, "replan", "error")


def test_replan_events_are_json_serializable():
    state = build(FlakyVerifier(), CountingPlanner()).run(COMPLEX, enable_memory=False)
    assert state.model_dump_json()
