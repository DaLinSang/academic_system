"""AnswerGenerator 的测试。

关注四件事：
  1. 有证据时，编号与页码必须进入提示词（否则模型无从引用）；
  2. 没证据时**不许编**，要么走 direct，要么明确说资料不足；
  3. 模型挂了要降级成"可读的原文列表"，而不是空答案；
  4. 越界引用不能被静默删除——它是 citation precision 的失败样本。
"""

from __future__ import annotations

import pytest

from app.agent.answer_generator import (
    NO_EVIDENCE_MESSAGE,
    LLMAnswerGenerator,
    extract_citation_indices,
)
from app.core.interfaces import AnswerGenerator
from app.core.types import (
    AgentState,
    Evidence,
    LLMResponse,
    MemoryItem,
    MemoryTier,
    RetrievalMethod,
    RouteDecision,
    RouteType,
    Usage,
)


class FakeLLM:
    def __init__(self, text: str = "答案 [1]", *, error: Exception | None = None,
                 total_tokens: int = 80) -> None:
        self.text = text
        self.error = error
        self.total_tokens = total_tokens
        self.prompts: list[str] = []
        self.calls = 0

    def generate(self, messages, temperature=0.0, max_tokens=None, json_mode=False):
        self.calls += 1
        self.prompts.append(messages[-1]["content"])
        if self.error is not None:
            raise self.error
        return LLMResponse(
            text=self.text,
            model="fake",
            usage=Usage(total_tokens=self.total_tokens, llm_calls=1, latency_ms=9.0),
        )


def make_evidence(count: int = 2, text: str = "RAG 结合参数化与非参数化记忆。") -> list[Evidence]:
    return [
        Evidence(
            chunk_id=f"c{index}",
            document_id="lec04",
            file_name="COMP7608B_Lec04.pdf",
            page_number=index + 6,
            text=text,
            score=1.0 - index * 0.1,
            retrieval_method=RetrievalMethod.RRF,
        )
        for index in range(1, count + 1)
    ]


def make_state(*, evidence=None, route: RouteType | None = None) -> AgentState:
    state = AgentState(question="讲义里 RAG 是什么？")
    state.evidence = evidence or []
    if route is not None:
        state.route = RouteDecision(
            route=route,
            need_retrieval=route != RouteType.DIRECT,
            need_planning=route == RouteType.COMPLEX_TASK,
        )
    return state


# --------------------------------------------------------------------------- #
# 接口一致性
# --------------------------------------------------------------------------- #
def test_satisfies_answer_generator_protocol():
    assert isinstance(LLMAnswerGenerator(FakeLLM()), AnswerGenerator)


# --------------------------------------------------------------------------- #
# 有证据：编号与页码必须进提示词
# --------------------------------------------------------------------------- #
def test_prompt_contains_numbered_evidence_with_page_numbers():
    llm = FakeLLM("RAG 结合两类记忆 [1]。")
    gen = LLMAnswerGenerator(llm)
    state = make_state(evidence=make_evidence(2))

    gen.generate(state)

    prompt = llm.prompts[0]
    assert "[1] 来源：COMP7608B_Lec04.pdf 第 7 页" in prompt
    assert "[2] 来源：COMP7608B_Lec04.pdf 第 8 页" in prompt
    assert "讲义里 RAG 是什么？" in prompt


def test_evidence_path_aggregates_usage():
    gen = LLMAnswerGenerator(FakeLLM(total_tokens=120))
    state = make_state(evidence=make_evidence(1))

    gen.generate(state)

    assert state.usage.total_tokens == 120
    assert state.usage.llm_calls == 1


def test_evidence_text_is_truncated():
    long_text = "甲" * 5000
    llm = FakeLLM("答案 [1]")
    gen = LLMAnswerGenerator(llm, max_chars_per_evidence=200)
    state = make_state(evidence=make_evidence(1, text=long_text))

    gen.generate(state)

    assert "…" in llm.prompts[0]
    assert len(llm.prompts[0]) < 1000


def test_evidence_count_is_capped():
    llm = FakeLLM("答案 [1]")
    gen = LLMAnswerGenerator(llm, max_evidence=2)
    state = make_state(evidence=make_evidence(5))

    gen.generate(state)

    assert "[2] 来源" in llm.prompts[0]
    assert "[3] 来源" not in llm.prompts[0]


def test_preferences_are_injected_but_marked_as_style_only():
    llm = FakeLLM("答案 [1]")
    gen = LLMAnswerGenerator(llm)
    state = make_state(evidence=make_evidence(1))
    state.memory_context = [
        MemoryItem(memory_id="m1", tier=MemoryTier.PREFERENCE, content="偏好中文并附页码")
    ]

    gen.generate(state)

    assert "偏好中文并附页码" in llm.prompts[0]
    assert "仅影响表达方式" in llm.prompts[0], "偏好不能影响事实内容，否则会污染证据grounding"


# --------------------------------------------------------------------------- #
# 无证据：不许编
# --------------------------------------------------------------------------- #
def test_direct_route_answers_without_evidence():
    llm = FakeLLM("Attention 是一种加权求和机制。")
    gen = LLMAnswerGenerator(llm)
    state = make_state(evidence=[], route=RouteType.DIRECT)

    answer = gen.generate(state)

    assert "加权求和" in answer
    assert llm.calls == 1
    assert state.usage.total_tokens == 80


def test_missing_evidence_on_retrieval_route_refuses_to_answer():
    llm = FakeLLM("我不该被调用")
    gen = LLMAnswerGenerator(llm)
    state = make_state(evidence=[], route=RouteType.DOCUMENT_RETRIEVAL)

    answer = gen.generate(state)

    assert answer == NO_EVIDENCE_MESSAGE
    assert llm.calls == 0, "没有证据时不应调用模型，否则必然产生幻觉"


def test_missing_evidence_on_complex_task_refuses_to_answer():
    llm = FakeLLM("我不该被调用")
    gen = LLMAnswerGenerator(llm)
    state = make_state(evidence=[], route=RouteType.COMPLEX_TASK)

    assert gen.generate(state) == NO_EVIDENCE_MESSAGE
    assert llm.calls == 0


def test_state_without_route_and_without_evidence_refuses():
    """路由缺失时按"需要证据"处理，宁可拒答也不编。"""
    llm = FakeLLM("我不该被调用")
    gen = LLMAnswerGenerator(llm)
    state = make_state(evidence=[], route=None)

    assert gen.generate(state) == NO_EVIDENCE_MESSAGE
    assert llm.calls == 0


# --------------------------------------------------------------------------- #
# 降级：模型挂了也要能给出可用信息
# --------------------------------------------------------------------------- #
def test_llm_failure_on_evidence_path_returns_extract():
    gen = LLMAnswerGenerator(FakeLLM(error=RuntimeError("timeout")))
    state = make_state(evidence=make_evidence(2))

    answer = gen.generate(state)

    assert "COMP7608B_Lec04.pdf 第 7 页" in answer
    assert "[1]" in answer and "[2]" in answer
    assert any(event.event == "error" for event in state.trace)


def test_llm_failure_on_direct_path_returns_apology():
    gen = LLMAnswerGenerator(FakeLLM(error=RuntimeError("timeout")))
    state = make_state(evidence=[], route=RouteType.DIRECT)

    answer = gen.generate(state)

    assert "模型调用失败" in answer
    assert "RuntimeError" in answer, "错误类型要暴露，方便排查"


def test_empty_model_response_falls_back_to_extract():
    gen = LLMAnswerGenerator(FakeLLM("   "))
    state = make_state(evidence=make_evidence(1))

    answer = gen.generate(state)

    assert "原文片段" in answer


# --------------------------------------------------------------------------- #
# 引用审计：越界编号要留证据
# --------------------------------------------------------------------------- #
def test_valid_citations_are_audited():
    gen = LLMAnswerGenerator(FakeLLM("第一点 [1]，第二点 [2]。"))
    state = make_state(evidence=make_evidence(2))

    gen.generate(state)

    audit = [event for event in state.trace if event.event == "citation_audit"]
    assert audit, "必须留下引用审计事件，C 的评测要读它"
    assert audit[0].detail["used"] == [1, 2]
    assert audit[0].detail["invalid"] == []


def test_out_of_range_citations_are_flagged_not_removed():
    gen = LLMAnswerGenerator(FakeLLM("第一点 [1]，还有一点 [7]。"))
    state = make_state(evidence=make_evidence(2))

    answer = gen.generate(state)

    assert "[7]" in answer, "越界引用不能删，删了评测就看不到 citation precision 的失败"
    audit = [event for event in state.trace if event.event == "citation_audit"][0]
    assert audit.detail["invalid"] == [7]


def test_extract_citation_indices_is_ordered_and_deduped():
    assert extract_citation_indices("a [2] b [1] c [2]") == [2, 1]
    assert extract_citation_indices("没有引用") == []


# --------------------------------------------------------------------------- #
# 参数校验
# --------------------------------------------------------------------------- #
def test_rejects_invalid_max_evidence():
    with pytest.raises(ValueError):
        LLMAnswerGenerator(FakeLLM(), max_evidence=0)


def test_rejects_too_small_evidence_window():
    with pytest.raises(ValueError):
        LLMAnswerGenerator(FakeLLM(), max_chars_per_evidence=10)
