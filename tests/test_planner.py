"""Planner 的测试。

除了常规的"能不能解析"，重点守三条容易被忽略的约束：
  1. 只有 complex_task 才拆解，其他两类必须返回空列表；
  2. 解析失败要返回**最小两步计划**，不能返回空——空列表会让 complex_task
     静默退化成普通检索，实验里完全看不出来；
  3. 重新编号后 depends_on 必须跟着映射，否则依赖关系全丢。
"""

from __future__ import annotations

import pytest

from app.core.interfaces import Planner
from app.core.types import (
    LLMResponse,
    MemoryItem,
    MemoryTier,
    RouteDecision,
    RouteType,
    Usage,
)
from app.planning.planner import LLMPlanner, fallback_plan

VALID_PLAN = """[
  {"step_id": 1, "description": "检索两篇论文中关于检索增强的段落",
   "tool": "retrieval", "query": "RAG 检索增强 方法", "depends_on": []},
  {"step_id": 2, "description": "提取各自的训练目标", "tool": "llm", "depends_on": [1]},
  {"step_id": 3, "description": "按方法维度对比并给出结论", "tool": "llm", "depends_on": [2]}
]"""


class FakeLLM:
    def __init__(self, text: str = VALID_PLAN, *, error: Exception | None = None,
                 total_tokens: int = 96) -> None:
        self.text = text
        self.error = error
        self.total_tokens = total_tokens
        self.calls = 0
        self.prompts: list[str] = []
        self.last_json_mode = False

    def generate(self, messages, temperature=0.0, max_tokens=None, json_mode=False):
        self.calls += 1
        self.last_json_mode = json_mode
        self.prompts.append(messages[-1]["content"])
        if self.error is not None:
            raise self.error
        return LLMResponse(
            text=self.text,
            model="fake",
            usage=Usage(total_tokens=self.total_tokens, llm_calls=1, latency_ms=15.0),
        )


def complex_route() -> RouteDecision:
    return RouteDecision(
        route=RouteType.COMPLEX_TASK, need_retrieval=True, need_planning=True
    )


def retrieval_route() -> RouteDecision:
    return RouteDecision(
        route=RouteType.DOCUMENT_RETRIEVAL, need_retrieval=True, need_planning=False
    )


# --------------------------------------------------------------------------- #
# 接口一致性
# --------------------------------------------------------------------------- #
def test_satisfies_planner_protocol():
    assert isinstance(LLMPlanner(FakeLLM()), Planner)


# --------------------------------------------------------------------------- #
# 只有 complex_task 才拆解
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("route", [retrieval_route(), RouteDecision(route=RouteType.DIRECT)])
def test_returns_empty_for_non_complex_route(route):
    llm = FakeLLM()
    steps = LLMPlanner(llm).plan("讲义里 RAG 是什么", route)

    assert steps == []
    assert llm.calls == 0, "非复杂任务不该消耗一次模型调用"


# --------------------------------------------------------------------------- #
# 正常解析
# --------------------------------------------------------------------------- #
def test_parses_valid_plan():
    steps = LLMPlanner(FakeLLM()).plan("比较 RAG 和 ReAct", complex_route())

    assert [step.step_id for step in steps] == [1, 2, 3]
    assert steps[0].tool == "retrieval"
    assert steps[0].query == "RAG 检索增强 方法"
    assert steps[1].depends_on == [1]
    assert steps[2].depends_on == [2]
    assert all(step.description for step in steps)


def test_enables_json_mode():
    llm = FakeLLM()
    LLMPlanner(llm).plan("比较两篇论文", complex_route())
    assert llm.last_json_mode is True


def test_parses_plan_inside_markdown_fence():
    llm = FakeLLM(f"好的：\n```json\n{VALID_PLAN}\n```\n以上。")
    steps = LLMPlanner(llm).plan("比较两篇论文", complex_route())
    assert len(steps) == 3


def test_parses_plan_with_prose_around_it():
    llm = FakeLLM(f"我拆成三步。{VALID_PLAN} 完毕。")
    steps = LLMPlanner(llm).plan("比较两篇论文", complex_route())
    assert len(steps) == 3


# --------------------------------------------------------------------------- #
# 归一化
# --------------------------------------------------------------------------- #
def test_step_ids_are_renumbered_and_dependencies_remapped():
    """模型常从 0 开始或跳号，直接透传会让 depends_on 全部失效。"""
    payload = """[
      {"step_id": 0, "description": "第一步", "tool": "retrieval", "query": "q1"},
      {"step_id": 5, "description": "第二步", "tool": "llm", "depends_on": [0]},
      {"step_id": 9, "description": "第三步", "tool": "llm", "depends_on": [5]}
    ]"""
    steps = LLMPlanner(FakeLLM(payload)).plan("比较两篇论文", complex_route())

    assert [step.step_id for step in steps] == [1, 2, 3]
    assert steps[1].depends_on == [1], "原来依赖 step_id=0，重排后应指向 1"
    assert steps[2].depends_on == [2], "原来依赖 step_id=5，重排后应指向 2"


def test_retrieval_step_without_query_gets_question():
    payload = '[{"step_id": 1, "description": "检索", "tool": "retrieval"}]'
    steps = LLMPlanner(FakeLLM(payload)).plan("比较 RAG 和 ReAct", complex_route())

    assert steps[0].query == "比较 RAG 和 ReAct", "缺 query 会让检索步骤白拆"


def test_unknown_tool_becomes_none():
    payload = '[{"step_id": 1, "description": "做点什么", "tool": "teleport"}]'
    steps = LLMPlanner(FakeLLM(payload)).plan("x", complex_route())
    assert steps[0].tool == "none"


def test_steps_without_description_are_dropped():
    payload = """[
      {"step_id": 1, "description": "", "tool": "llm"},
      {"step_id": 2, "description": "有效步骤", "tool": "llm"}
    ]"""
    steps = LLMPlanner(FakeLLM(payload)).plan("x", complex_route())
    assert len(steps) == 1
    assert steps[0].description == "有效步骤"
    assert steps[0].step_id == 1


def test_self_dependency_is_removed():
    payload = '[{"step_id": 1, "description": "步骤", "tool": "llm", "depends_on": [1]}]'
    steps = LLMPlanner(FakeLLM(payload)).plan("x", complex_route())
    assert steps[0].depends_on == []


def test_max_steps_is_enforced():
    payload = (
        "["
        + ",".join(
            f'{{"step_id": {index}, "description": "步骤{index}", "tool": "llm"}}'
            for index in range(1, 11)
        )
        + "]"
    )
    steps = LLMPlanner(FakeLLM(payload), max_steps=3).plan("x", complex_route())
    assert len(steps) == 3


def test_rejects_invalid_max_steps():
    with pytest.raises(ValueError):
        LLMPlanner(FakeLLM(), max_steps=1)


# --------------------------------------------------------------------------- #
# 降级：必须返回可用计划，不能返回空
# --------------------------------------------------------------------------- #
def test_invalid_json_falls_back_to_minimal_plan():
    steps = LLMPlanner(FakeLLM("我拆不出来。")).plan("比较两篇论文", complex_route())

    assert len(steps) == 2
    assert steps[0].tool == "retrieval"
    assert steps[0].query == "比较两篇论文"
    assert steps[1].tool == "llm"


def test_llm_failure_falls_back_to_minimal_plan():
    steps = LLMPlanner(FakeLLM(error=RuntimeError("timeout"))).plan(
        "比较两篇论文", complex_route()
    )
    assert len(steps) == 2


def test_empty_array_falls_back_to_minimal_plan():
    steps = LLMPlanner(FakeLLM("[]")).plan("比较两篇论文", complex_route())
    assert len(steps) == 2, "空计划会让 complex_task 静默退化成普通检索"


def test_fallback_plan_is_usable():
    steps = fallback_plan("比较 RAG 和 ReAct")
    assert [step.step_id for step in steps] == [1, 2]
    assert steps[0].query == "比较 RAG 和 ReAct"
    assert steps[1].depends_on == [1]


# --------------------------------------------------------------------------- #
# 用量与上下文
# --------------------------------------------------------------------------- #
def test_last_usage_records_cost():
    planner = LLMPlanner(FakeLLM(total_tokens=77))
    planner.plan("比较两篇论文", complex_route())
    assert planner.last_usage.total_tokens == 77


def test_last_usage_zero_when_not_complex():
    planner = LLMPlanner(FakeLLM(total_tokens=77))
    planner.plan("讲义里 RAG 是什么", retrieval_route())
    assert planner.last_usage.total_tokens == 0


def test_memory_context_reaches_prompt():
    llm = FakeLLM()
    memory = [
        MemoryItem(memory_id="m1", tier=MemoryTier.TASK_STATE, content="正在写 RAG 综述")
    ]
    LLMPlanner(llm).plan("写一份综述", complex_route(), memory)
    assert "正在写 RAG 综述" in llm.prompts[0]
