"""Router 的测试。

重点不是"模型判断准不准"（那要靠 C 的标注集评测），而是：
  1. 任何模型输出都不能让路由崩溃；
  2. 归一化后三类各自的约束必须成立（否则 RQ1 的指标无法量化）；
  3. 路由消耗的 token 必须能传回 AgentState（否则 RQ4 成本统计偏低）。
"""

from __future__ import annotations

import pytest

from app.agent.factory import build_orchestrator
from app.core.interfaces import Router
from app.core.mocks import (
    MockAnswerGenerator,
    MockMemoryManager,
    MockMemoryStore,
    MockPlanner,
    MockRetriever,
    MockVerifier,
)
from app.core.types import (
    LLMResponse,
    MemoryItem,
    MemoryTier,
    RouteType,
    Usage,
)
from app.routing.router import LLMRouter, RuleBasedRouter, _extract_json_object


class FakeLLM:
    """按需返回固定文本或抛异常的假模型。"""

    def __init__(self, text: str = "", *, error: Exception | None = None,
                 total_tokens: int = 42) -> None:
        self.text = text
        self.error = error
        self.total_tokens = total_tokens
        self.calls = 0
        self.last_json_mode = False

    def generate(self, messages, temperature=0.0, max_tokens=None, json_mode=False):
        self.calls += 1
        self.last_json_mode = json_mode
        if self.error is not None:
            raise self.error
        return LLMResponse(
            text=self.text,
            model="fake-model",
            usage=Usage(
                prompt_tokens=self.total_tokens // 2,
                completion_tokens=self.total_tokens - self.total_tokens // 2,
                total_tokens=self.total_tokens,
                cost_usd=0.0001,
                llm_calls=1,
                latency_ms=12.0,
            ),
        )


VALID_COMPLEX = (
    '{"route": "complex_task", "need_retrieval": true, "need_memory": false, '
    '"need_planning": true, "confidence": 0.86, "reason": "用户要求比较两篇论文的方法"}'
)


# --------------------------------------------------------------------------- #
# 接口一致性
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "router",
    [LLMRouter(FakeLLM(VALID_COMPLEX)), RuleBasedRouter()],
)
def test_satisfies_router_protocol(router):
    assert isinstance(router, Router)


# --------------------------------------------------------------------------- #
# 正常解析
# --------------------------------------------------------------------------- #
def test_parses_valid_json():
    router = LLMRouter(FakeLLM(VALID_COMPLEX))
    decision = router.route("比较 RAG 和 ReAct 的方法差异")

    assert decision.route == RouteType.COMPLEX_TASK
    assert decision.need_planning is True
    assert decision.need_retrieval is True
    assert decision.confidence == pytest.approx(0.86)
    assert "比较" in decision.reason


def test_enables_json_mode():
    llm = FakeLLM(VALID_COMPLEX)
    LLMRouter(llm).route("比较两篇论文")
    assert llm.last_json_mode is True, "路由必须开 json_mode，否则模型容易夹带解释"


def test_parses_json_inside_markdown_fence():
    llm = FakeLLM(f"好的，分析如下：\n```json\n{VALID_COMPLEX}\n```\n希望有帮助。")
    decision = LLMRouter(llm).route("比较两篇论文")
    assert decision.route == RouteType.COMPLEX_TASK


def test_parses_json_with_prose_on_both_sides():
    llm = FakeLLM(f"我认为属于复杂任务。{VALID_COMPLEX} 以上。")
    decision = LLMRouter(llm).route("比较两篇论文")
    assert decision.route == RouteType.COMPLEX_TASK


def test_accepts_route_aliases():
    llm = FakeLLM('{"route": "rag", "need_retrieval": true, "confidence": 0.7}')
    decision = LLMRouter(llm).route("讲义里 RAG 是什么")
    assert decision.route == RouteType.DOCUMENT_RETRIEVAL


# --------------------------------------------------------------------------- #
# 归一化：三类各自的硬约束
# --------------------------------------------------------------------------- #
def test_direct_never_retrieves_even_if_model_says_so():
    """模型自相矛盾时必须修正，否则"不必要检索率"无法统计。"""
    llm = FakeLLM(
        '{"route": "direct", "need_retrieval": true, "need_planning": true, '
        '"confidence": 0.9, "reason": "常识问题"}'
    )
    decision = LLMRouter(llm).route("什么是 transformer")
    assert decision.route == RouteType.DIRECT
    assert decision.need_retrieval is False
    assert decision.need_planning is False


def test_document_retrieval_forces_retrieval_true():
    llm = FakeLLM(
        '{"route": "document_retrieval", "need_retrieval": false, "confidence": 0.8}'
    )
    decision = LLMRouter(llm).route("讲义里讲了什么")
    assert decision.need_retrieval is True
    assert decision.need_planning is False


def test_complex_task_forces_planning_true():
    llm = FakeLLM(
        '{"route": "complex_task", "need_retrieval": true, "need_planning": false, '
        '"confidence": 0.8}'
    )
    decision = LLMRouter(llm).route("写一份综述")
    assert decision.need_planning is True


def test_document_retrieval_downgraded_without_documents():
    """没有可用文档时不可能检索，必须降级为 direct。"""
    llm = FakeLLM(
        '{"route": "document_retrieval", "need_retrieval": true, "confidence": 0.9}'
    )
    decision = LLMRouter(llm).route("这篇论文讲了什么", has_documents=False)
    assert decision.route == RouteType.DIRECT
    assert decision.need_retrieval is False


def test_complex_task_without_documents_does_not_retrieve():
    llm = FakeLLM(
        '{"route": "complex_task", "need_retrieval": true, "need_planning": true, '
        '"confidence": 0.8}'
    )
    decision = LLMRouter(llm).route("写一份综述", has_documents=False)
    assert decision.route == RouteType.COMPLEX_TASK
    assert decision.need_retrieval is False
    assert decision.need_planning is True


def test_confidence_is_clamped():
    high = LLMRouter(FakeLLM('{"route": "direct", "confidence": 9.9}')).route("x")
    low = LLMRouter(FakeLLM('{"route": "direct", "confidence": -3}')).route("x")
    assert high.confidence == 1.0
    assert low.confidence == 0.0


def test_bool_strings_are_coerced():
    llm = FakeLLM(
        '{"route": "document_retrieval", "need_memory": "true", '
        '"need_retrieval": "false", "confidence": "0.75"}'
    )
    decision = LLMRouter(llm).route("讲义里 RAG 是什么")
    assert decision.need_memory is True
    assert decision.need_retrieval is True  # document_retrieval 强制覆盖
    assert decision.confidence == pytest.approx(0.75)


def test_missing_reason_gets_placeholder():
    llm = FakeLLM('{"route": "direct", "confidence": 0.5}')
    decision = LLMRouter(llm).route("你好")
    assert decision.reason.strip(), "reason 不能为空，报告要用"


# --------------------------------------------------------------------------- #
# 降级：任何异常都必须返回合法决策
# --------------------------------------------------------------------------- #
def test_invalid_json_falls_back_to_heuristic():
    router = LLMRouter(FakeLLM("我无法判断这个问题。"))
    decision = router.route("比较这两篇论文")

    assert decision.route == RouteType.COMPLEX_TASK, "启发式应识别出比较类任务"
    assert "无法解析" in decision.reason
    assert decision.confidence <= 0.5, "降级结果不应表现出高置信"


def test_llm_failure_falls_back_to_heuristic():
    router = LLMRouter(FakeLLM(error=RuntimeError("connection reset")))
    decision = router.route("讲义里第几页讲了 RRF")

    assert decision.route == RouteType.DOCUMENT_RETRIEVAL
    assert "模型调用失败" in decision.reason


def test_heuristic_does_not_retrieve_for_generic_concept_questions():
    """「什么是 X」是典型的不需要检索的问题。

    若把它算作检索，RQ1 的"不必要检索率"会被系统性抬高，指标失去意义。
    """
    router = RuleBasedRouter()
    for question in ("什么是 attention", "解释一下 transformer 的定义", "介绍一下 RAG"):
        assert router.route(question).route == RouteType.DIRECT, question


def test_heuristic_retrieves_on_strong_document_reference():
    router = RuleBasedRouter()
    for question in ("讲义里 RRF 怎么算的", "我的笔记第几页提到 BM25", "这篇论文的方法是什么"):
        assert router.route(question).route == RouteType.DOCUMENT_RETRIEVAL, question


def test_unknown_route_value_falls_back():
    router = LLMRouter(FakeLLM('{"route": "teleport", "confidence": 0.9}'))
    decision = router.route("比较两篇论文")
    assert decision.route == RouteType.COMPLEX_TASK
    assert "无法解析" in decision.reason


def test_json_extraction_rejects_unbalanced():
    with pytest.raises(ValueError):
        _extract_json_object('{"route": "direct", "reason": "未闭合')


def test_json_extraction_handles_braces_inside_strings():
    payload = _extract_json_object('{"route": "direct", "reason": "包含 { 和 } 的说明"}')
    assert payload["route"] == "direct"


# --------------------------------------------------------------------------- #
# 用量回收：RQ4 的成本不能漏
# --------------------------------------------------------------------------- #
def test_last_usage_records_llm_cost():
    router = LLMRouter(FakeLLM(VALID_COMPLEX, total_tokens=128))
    router.route("比较两篇论文")
    assert router.last_usage.total_tokens == 128
    assert router.last_usage.llm_calls == 1


def test_last_usage_reset_on_failure():
    router = LLMRouter(FakeLLM(error=RuntimeError("boom")))
    router.route("x")
    assert router.last_usage.total_tokens == 0, "失败时不应留下上一次的用量"


def test_orchestrator_absorbs_router_usage():
    """路由消耗的 token 必须进入 AgentState.usage，否则 B1–B4 成本对比失真。"""
    llm = FakeLLM(VALID_COMPLEX, total_tokens=100)
    orch = build_orchestrator(
        router=LLMRouter(llm),
        planner=MockPlanner(),
        retriever=MockRetriever(),
        memory_store=MockMemoryStore(),
        memory_manager=MockMemoryManager(),
        verifier=MockVerifier(),
        answer_generator=MockAnswerGenerator(),
        llm=llm,
    )
    state = orch.run("比较 RAG 和 ReAct 的方法差异")

    assert state.usage.total_tokens >= 100
    assert state.usage.llm_calls >= 1
    assert state.route is not None and state.route.route == RouteType.COMPLEX_TASK


def test_orchestrator_works_with_rule_based_router():
    """无 LLM 的基线也要能跑通，用于成本对比。"""
    orch = build_orchestrator(
        router=RuleBasedRouter(),
        planner=MockPlanner(),
        retriever=MockRetriever(),
        memory_store=MockMemoryStore(),
        memory_manager=MockMemoryManager(),
        verifier=MockVerifier(),
        answer_generator=MockAnswerGenerator(),
        llm=FakeLLM(),
    )
    state = orch.run("讲义里 RAG 是什么")
    assert state.route is not None
    assert state.route.route == RouteType.DOCUMENT_RETRIEVAL


# --------------------------------------------------------------------------- #
# 记忆上下文会进入提示词
# --------------------------------------------------------------------------- #
def test_memory_context_is_passed_to_prompt():
    captured: list[str] = []

    class CapturingLLM(FakeLLM):
        def generate(self, messages, temperature=0.0, max_tokens=None, json_mode=False):
            captured.append(messages[-1]["content"])
            return super().generate(messages, temperature, max_tokens, json_mode)

    memory = [
        MemoryItem(
            memory_id="m1",
            tier=MemoryTier.PREFERENCE,
            content="用户偏好中文回答并附页码",
        )
    ]
    LLMRouter(CapturingLLM(VALID_COMPLEX)).route("比较两篇论文", memory_context=memory)

    assert "用户偏好中文回答并附页码" in captured[0]
