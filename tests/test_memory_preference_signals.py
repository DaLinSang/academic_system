"""Issue #31 回归：长期偏好识别 + 预筛误命中修复。

两个真实 bug：
1. 预筛漏掉"以后回答时""每次回答"等稳定偏好表达 → 不生成候选
2. `"我是"` 用裸子串匹配，在「告诉我是自己查的」里误命中 → 一次性请求也调模型

另外钉死提示词里的 few-shot：模型必须把"对未来回答方式的要求"判成 preference。
"""

from __future__ import annotations

import json

import pytest

from app.core.types import LLMResponse, MemoryTier, Usage
from app.memory import LLMMemoryManager, looks_memorable
from app.memory.manager import _SYSTEM_PROMPT


# --------------------------------------------------------------------------- #
# 闸 1：稳定偏好必须命中
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "question",
    [
        "请你以后回答之前告诉我是否能在我上传的文档中检索到，检索不到告诉我你是自己查阅生成的。",
        "请你在每次回答问题前加上'好的'",
        "以后回答时请先说明是否引用了我的文档",
        "回答前请先告诉我是基于文档还是模型知识",
        "我正在写 RAG 报告，实验部分还没有完成，请记住。",
        "我是港大 COMP7608B 的学生",
        "请记住我的偏好",
        "From now on, always respond in English",
        "Before answering, tell me whether you used my documents",
    ],
)
def test_stable_preferences_pass_gate(question):
    assert looks_memorable(question) is True, f"稳定偏好被预筛漏掉: {question}"


@pytest.mark.parametrize(
    "question",
    [
        "请解释一下 RAG",
        "帮我回答这题",
        "什么是 attention",
        "今天天气不错",
        "帮我总结这段",
    ],
)
def test_one_off_requests_are_rejected(question):
    assert looks_memorable(question) is False, f"一次性请求误入预筛: {question}"


@pytest.mark.parametrize(
    "question",
    [
        "告诉我是自己查阅生成的",        # "我是" 命中的经典误判
        "他说我是错的",
        "他告诉我是自己查的",
    ],
)
def test_short_signal_requires_boundary(question):
    """短信号在词中间出现时不算，避免"告诉我是..."误命中。"""
    assert looks_memorable(question) is False, f"短信号误命中: {question}"


def test_comma_boundary_still_admits_short_signal():
    """逗号后的"我是"仍算句首，这种边缘误判交给 LLM 闸 2 拦，不值得复杂化。"""
    assert looks_memorable("别误会，我是说如果") is True


def test_short_signal_at_clause_boundary_still_works():
    assert looks_memorable("好的，我是港大的学生") is True


# --------------------------------------------------------------------------- #
# 提示词：few-shot 必须把未来偏好判成 preference
# --------------------------------------------------------------------------- #
def test_prompt_has_preference_few_shot():
    assert "以后回答时请先说明是否引用了我的文档" in _SYSTEM_PROMPT
    assert "每次回答问题前" in _SYSTEM_PROMPT
    assert "对未来所有回答方式的要求" in _SYSTEM_PROMPT


def test_prompt_states_instruction_can_be_preference():
    assert "即使句子形如指令" in _SYSTEM_PROMPT


# --------------------------------------------------------------------------- #
# 抽取结果：候选、去重、敏感
# --------------------------------------------------------------------------- #
class ScriptedLLM:
    def __init__(self, payload: list[dict] | None) -> None:
        self.text = json.dumps(payload if payload is not None else [], ensure_ascii=False)
        self.calls = 0

    def generate(self, messages, temperature=0.0, max_tokens=None, json_mode=False):
        self.calls += 1
        return LLMResponse(text=self.text, model="scripted",
                           usage=Usage(total_tokens=40, llm_calls=1))


_PREFERENCE = [{
    "tier": "preference",
    "content": "用户希望回答时先说明是否引用了上传文档",
    "importance": 0.9,
    "reason": "对未来所有回答方式的要求",
}]


def test_preference_candidate_is_extracted():
    mgr = LLMMemoryManager(ScriptedLLM(_PREFERENCE))
    got = mgr.extract_candidates("以后回答时请先说明是否引用了我的文档", "好的")

    assert len(got) == 1
    assert got[0].tier is MemoryTier.PREFERENCE
    assert "上传文档" in got[0].content
    assert mgr.last_stats["accepted"] == 1


def test_duplicate_preference_is_dropped():
    from app.core.types import MemoryItem

    existing = [MemoryItem(memory_id="m1", tier=MemoryTier.PREFERENCE,
                           content="用户希望回答时先说明是否引用了上传文档")]
    mgr = LLMMemoryManager(ScriptedLLM(_PREFERENCE))
    got = mgr.extract_candidates("以后回答时请先说明是否引用了我的文档", "好的", existing)

    assert got == []
    assert mgr.last_stats["dropped_duplicate"] == 1


def test_sensitive_value_is_dropped():
    payload = [{
        "tier": "preference",
        "content": "用户的邮箱是 test@example.com",
        "importance": 0.9,
        "reason": "联系方式",
    }]
    mgr = LLMMemoryManager(ScriptedLLM(payload))
    got = mgr.extract_candidates("请记住我的邮箱是 test@example.com", "好的")

    assert got == []
    assert mgr.last_stats["dropped_sensitive"] == 1


def test_model_returning_empty_is_distinguishable_from_gate_skip():
    mgr = LLMMemoryManager(ScriptedLLM([]))
    mgr.extract_candidates("以后回答时请先说明", "好的")
    assert mgr.last_stats["skipped"] is None, "过了闸 1 但模型没产出，应记为 proposed=0 而非 skipped"

    mgr2 = LLMMemoryManager(ScriptedLLM([]))
    mgr2.extract_candidates("请解释一下 RAG", "好的")
    assert mgr2.last_stats["skipped"] == "no_signal"
