"""Orchestrator 的工具调用记录与步骤状态回填。

这两个字段此前是死的：
  - `tool_results` 定义了但 `record_tool()` 零调用 → 恒为空
  - `PlanStep.status` 只有 MockPlanner 硬编码 DONE → 真实执行后不回填

后果是"失败案例分析"拿不到工具级数据，报告里也讲不清"哪一步没跑成"。
本文件守的就是这两条线。
"""

from __future__ import annotations

from app.agent.factory import build_orchestrator
from app.core.mocks import (
    MockAnswerGenerator,
    MockLLM,
    MockMemoryManager,
    MockMemoryStore,
    MockVerifier,
)
from app.core.types import (
    AgentState,
    Evidence,
    PlanStep,
    RetrievalMethod,
    RouteDecision,
    RouteType,
    StepStatus,
)
from app.routing.router import RuleBasedRouter


class RecordingRetriever:
    """按 query 返回固定结果，并记录调用次数。"""

    def __init__(self, count: int = 2, *, error: Exception | None = None) -> None:
        self.count = count
        self.error = error
        self.queries: list[str] = []

    def retrieve(self, query: str, top_k: int = 5) -> list[Evidence]:
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        return [
            Evidence(
                chunk_id=f"{query}-c{i}",
                document_id="d1",
                file_name="Lec04.pdf",
                page_number=i + 1,
                text=f"{query} 的相关段落 {i}",
                score=1.0 - i * 0.1,
                retrieval_method=RetrievalMethod.MOCK,
            )
            for i in range(self.count)
        ]

    def index_document(self, pdf_path: str, document_id: str | None = None) -> int:
        return 0


class FixedPlanner:
    """返回固定的多步计划，其中包含 retrieval 与 llm 两类步骤。"""

    def __init__(self, steps: list[PlanStep] | None = None) -> None:
        self.steps = steps or [
            PlanStep(step_id=1, description="检索 RAG 段落", tool="retrieval", query="RAG"),
            PlanStep(step_id=2, description="检索 ReAct 段落", tool="retrieval", query="ReAct"),
            PlanStep(step_id=3, description="对比两者", tool="llm", depends_on=[1, 2]),
        ]

    def plan(self, question, route, memory_context=None):
        return [s.model_copy() for s in self.steps]


def build(planner=None, retriever=None, *, verification=False):
    return build_orchestrator(
        router=RuleBasedRouter(),
        planner=planner or FixedPlanner(),
        retriever=retriever or RecordingRetriever(),
        memory_store=MockMemoryStore(),
        memory_manager=MockMemoryManager(),
        verifier=MockVerifier(),
        answer_generator=MockAnswerGenerator(),
        llm=MockLLM(),
    )


COMPLEX = "比较 RAG 和 ReAct 的方法差异"


# --------------------------------------------------------------------------- #
# tool_results
# --------------------------------------------------------------------------- #
def test_successful_retrieval_is_recorded():
    state = build().run(COMPLEX, enable_memory=False)

    assert state.tool_results, "检索成功也必须记录，否则没有工具级数据"
    first = state.tool_results[0]
    assert first.tool_name == "retrieval"
    assert first.success is True
    assert first.output["count"] == 2
    assert first.metadata["query"] == "RAG"
    assert first.latency_ms >= 0


def test_each_retrieval_step_produces_one_tool_result():
    state = build().run(COMPLEX, enable_memory=False)

    # 计划里有两个 retrieval 步骤
    assert len(state.tool_results) == 2
    assert [r.metadata["query"] for r in state.tool_results] == ["RAG", "ReAct"]
    assert [r.metadata["step_id"] for r in state.tool_results] == [1, 2]


def test_failed_retrieval_is_recorded_and_adds_error():
    retriever = RecordingRetriever(error=RuntimeError("db down"))
    state = build(retriever=retriever).run(COMPLEX, enable_memory=False)

    assert len(state.tool_results) == 2, "失败也要逐个记录"
    assert all(r.success is False for r in state.tool_results)
    assert all("RuntimeError" in (r.error or "") for r in state.tool_results)
    # record_tool 失败时会写入 errors
    assert any("retrieval" in e for e in state.errors)


def test_failure_does_not_abort_remaining_steps():
    retriever = RecordingRetriever(error=RuntimeError("db down"))
    state = build(retriever=retriever).run(COMPLEX, enable_memory=False)

    assert retriever.queries == ["RAG", "ReAct"], "第一步失败不应中断第二步"
    assert state.final_answer.strip()


# --------------------------------------------------------------------------- #
# 步骤状态回填
# --------------------------------------------------------------------------- #
def test_retrieval_steps_marked_done_with_summary():
    state = build().run(COMPLEX, enable_memory=False)

    retrieval_steps = [s for s in state.plan if s.tool == "retrieval"]
    assert all(s.status is StepStatus.DONE for s in retrieval_steps)
    assert all("命中 2 个片段" in (s.result_summary or "") for s in retrieval_steps)


def test_retrieval_steps_marked_failed_on_error():
    state = build(retriever=RecordingRetriever(error=RuntimeError("db down"))).run(
        COMPLEX, enable_memory=False
    )

    retrieval_steps = [s for s in state.plan if s.tool == "retrieval"]
    assert all(s.status is StepStatus.FAILED for s in retrieval_steps)
    assert all("RuntimeError" in (s.result_summary or "") for s in retrieval_steps)


def test_llm_steps_stay_pending():
    """llm 类步骤当前不单独执行，保持 PENDING 是如实反映，不能假装完成。"""
    state = build().run(COMPLEX, enable_memory=False)

    llm_steps = [s for s in state.plan if s.tool == "llm"]
    assert llm_steps, "计划里应有 llm 步骤"
    assert all(s.status is StepStatus.PENDING for s in llm_steps)


def test_current_step_counts_processed_steps():
    state = build().run(COMPLEX, enable_memory=False)

    processed = sum(1 for s in state.plan if s.status is not StepStatus.PENDING)
    assert state.current_step == processed == 2


# --------------------------------------------------------------------------- #
# 无计划时的退化路径
# --------------------------------------------------------------------------- #
def test_falls_back_to_question_when_plan_has_no_retrieval_step():
    planner = FixedPlanner([PlanStep(step_id=1, description="直接回答", tool="llm")])
    retriever = RecordingRetriever(count=1)
    state = build(planner=planner, retriever=retriever).run(
        "讲义里 RAG 是什么", enable_memory=False
    )

    assert retriever.queries == ["讲义里 RAG 是什么"], "没有检索步骤时用原问题"
    assert len(state.tool_results) == 1
    assert state.tool_results[0].metadata["step_id"] is None


def test_direct_route_records_no_tool_result():
    state = build().run("什么是 attention", enable_memory=False)

    assert state.route is not None and state.route.is_direct
    assert state.tool_results == [], "direct 路径不该有检索工具调用"


# --------------------------------------------------------------------------- #
# 与 trace 的一致性
# --------------------------------------------------------------------------- #
def test_retrieval_end_event_reports_tool_count():
    state = build().run(COMPLEX, enable_memory=False)

    ends = [e for e in state.trace if e.stage == "retrieval" and e.event == "end"]
    assert ends
    assert ends[0].detail["tools_recorded"] == 2
    assert ends[0].detail["queries"] == 2


def test_tool_result_metadata_is_json_serializable():
    state = build().run(COMPLEX, enable_memory=False)
    # 评测要把整个 state 写 JSONL，tool_results 不能有不可序列化的东西
    assert state.model_dump_json()


def test_state_with_no_plan_still_serializes():
    state = AgentState(question="x")
    state.route = RouteDecision(route=RouteType.DIRECT)
    assert "tool_results" in state.model_dump_json()
