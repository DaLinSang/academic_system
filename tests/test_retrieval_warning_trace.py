"""检索降级必须在 trace 里可见。

B 的 #35 让 HybridRetriever 在向量失败时退回 BM25-only，并把原因放在
`last_warning`。如果只看 `evidence`，降级轮次和正常 RRF 轮次长得一样，
C 算 RQ1/RQ3 时会把两个不同的系统混在一起统计。
"""

from __future__ import annotations

from app.agent.factory import build_mock_orchestrator, build_orchestrator
from app.agent.orchestrator import MODE_PRESETS
from app.core.mocks import (
    MockAnswerGenerator,
    MockLLM,
    MockMemoryManager,
    MockMemoryStore,
    MockPlanner,
    MockVerifier,
)
from app.core.types import Evidence, PlanStep, RetrievalMethod
from app.routing.router import RuleBasedRouter


class DegradedRetriever:
    """模拟向量检索失败、退回 BM25 的检索器。"""

    def __init__(self, warning: str | None = None) -> None:
        self.last_warning = warning
        self.calls = 0

    def retrieve(self, query: str, top_k: int = 5) -> list[Evidence]:
        self.calls += 1
        return [
            Evidence(
                chunk_id=f"c{i}",
                document_id="d1",
                file_name="Lec04.pdf",
                page_number=i + 1,
                text=f"{query} 的片段 {i}",
                score=1.0,
                retrieval_method=RetrievalMethod.BM25,
            )
            for i in range(2)
        ]

    def index_document(self, pdf_path: str, document_id: str | None = None) -> int:
        return 0


def build(retriever):
    return build_orchestrator(
        router=RuleBasedRouter(),
        planner=MockPlanner(),
        retriever=retriever,
        memory_store=MockMemoryStore(),
        memory_manager=MockMemoryManager(),
        verifier=MockVerifier(),
        answer_generator=MockAnswerGenerator(),
        llm=MockLLM(),
    )


def _end_event(state):
    return next(
        e for e in state.trace if e.stage == "retrieval" and e.event == "end"
    )


def test_warning_is_recorded_in_end_event():
    retriever = DegradedRetriever("Vector retrieval unavailable; using BM25 only")
    state = build(retriever).run("讲义里 RAG 是什么", **MODE_PRESETS["B2"])

    end = _end_event(state)
    assert end.detail["degraded"] is True
    assert "BM25 only" in end.detail["warning"]


def test_separate_degraded_event_is_emitted():
    retriever = DegradedRetriever("Ollama timeout")
    state = build(retriever).run("讲义里 RAG 是什么", **MODE_PRESETS["B2"])

    degraded = [e for e in state.trace if e.event == "degraded"]
    assert len(degraded) == 1
    assert degraded[0].detail["reason"] == "Ollama timeout"


def test_normal_retrieval_marks_not_degraded():
    state = build(DegradedRetriever(None)).run("讲义里 RAG 是什么", **MODE_PRESETS["B2"])

    end = _end_event(state)
    assert end.detail["degraded"] is False
    assert end.detail["warning"] is None


def test_mock_retriever_without_warning_attribute_still_works():
    """MockRetriever 没有 last_warning，getattr 必须静默跳过。"""
    state = build_mock_orchestrator().run("讲义里 RAG 是什么", **MODE_PRESETS["B2"])

    assert _end_event(state).detail["degraded"] is False


def test_degraded_run_still_returns_evidence():
    """降级不该影响回答，只是把系统状态如实记录下来。"""
    state = build(DegradedRetriever("vector down")).run(
        "讲义里 RAG 是什么", **MODE_PRESETS["B2"]
    )

    assert len(state.evidence) == 2
    assert state.final_answer.strip()
    assert state.errors == [], "降级是 warning，不是 error"


# --------------------------------------------------------------------------- #
# C review #38：多查询场景的警告不能被最后一次调用清掉
# --------------------------------------------------------------------------- #
class FlakyWarningRetriever:
    """模拟 HybridRetriever：每次调用开头把 last_warning 重置。

    第一次查询向量失败降级，第二次查询正常。
    """

    def __init__(self) -> None:
        self.last_warning: str | None = None
        self.calls = 0

    def retrieve(self, query: str, top_k: int = 5):
        self.calls += 1
        self.last_warning = (
            "Vector retrieval unavailable; using BM25 only" if self.calls == 1 else None
        )
        return [
            Evidence(
                chunk_id=f"{query}-c{i}",
                document_id="d1",
                file_name="Lec04.pdf",
                page_number=i + 1,
                text=f"{query} 片段",
                score=0.5,
                retrieval_method=RetrievalMethod.BM25,
            )
            for i in range(1)
        ]

    def index_document(self, pdf_path: str, document_id: str | None = None) -> int:
        return 0


class TwoStepPlanner:
    def plan(self, question, route, memory_context=None):
        return [
            PlanStep(step_id=1, description="检索一", tool="retrieval", query="first"),
            PlanStep(step_id=2, description="检索二", tool="retrieval", query="second"),
        ]


def test_multi_query_warning_is_not_lost():
    """第一个查询降级、第二个正常，最终仍必须标记 degraded=True。"""
    from app.agent.orchestrator import MODE_PRESETS
    from app.core.mocks import (
        MockAnswerGenerator,
        MockLLM,
        MockMemoryManager,
        MockMemoryStore,
        MockVerifier,
    )
    from app.routing.router import RuleBasedRouter

    retriever = FlakyWarningRetriever()
    orch = build_orchestrator(
        router=RuleBasedRouter(),
        planner=TwoStepPlanner(),
        retriever=retriever,
        memory_store=MockMemoryStore(),
        memory_manager=MockMemoryManager(),
        verifier=MockVerifier(),
        answer_generator=MockAnswerGenerator(),
        llm=MockLLM(),
    )
    state = orch.run("比较 RAG 和 ReAct", **MODE_PRESETS["B3"])

    assert retriever.calls == 2
    end = [e for e in state.trace if e.stage == "retrieval" and e.event == "end"][0]
    assert end.detail["degraded"] is True
    assert "BM25 only" in end.detail["warning"]
    assert "first" in end.detail["degraded_queries"]


def test_each_degraded_event_has_query_and_step_id():
    from app.agent.orchestrator import MODE_PRESETS
    from app.core.mocks import (
        MockAnswerGenerator,
        MockLLM,
        MockMemoryManager,
        MockMemoryStore,
        MockVerifier,
    )
    from app.routing.router import RuleBasedRouter

    orch = build_orchestrator(
        router=RuleBasedRouter(),
        planner=TwoStepPlanner(),
        retriever=FlakyWarningRetriever(),
        memory_store=MockMemoryStore(),
        memory_manager=MockMemoryManager(),
        verifier=MockVerifier(),
        answer_generator=MockAnswerGenerator(),
        llm=MockLLM(),
    )
    state = orch.run("比较 RAG 和 ReAct", **MODE_PRESETS["B3"])

    degraded = [e for e in state.trace if e.stage == "retrieval" and e.event == "degraded"]
    assert len(degraded) == 1
    assert degraded[0].detail["query"] == "first"
    assert degraded[0].detail["step_id"] == 1
