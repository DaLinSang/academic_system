"""Issue #83：网页搜索已执行时，模型不得谎称"无法联网"。

守：
1. web/end count>0 时，回答里的"我无法联网"类表述被替换为准确说明
2. 没有 web 执行 / count=0 时不误改
3. 不含否认表述的回答原样返回
"""

from __future__ import annotations

from app.agent.answer_generator import LLMAnswerGenerator
from app.core.types import AgentState, TraceEvent


def state_with_web(count, web_executed=True):
    s = AgentState(question="搜最新 agentic ai 论文")
    if web_executed:
        s.trace.append(TraceEvent(
            stage="web_search", event="end", detail={"count": count, "warning": None}
        ))
    return s


def test_web_executed_but_model_denies_is_corrected():
    s = state_with_web(3)
    answer = "现有证据无关。\n另外，我无法联网检索，因此不能按你的要求去搜索最新论文。"
    fixed = LLMAnswerGenerator._fix_web_honesty(answer, s)
    assert "无法联网" not in fixed
    assert "没有可作为证据的网页来源" in fixed
    assert "返回 3 条结果" in fixed
    assert any(e.event == "web_honesty_fix" for e in s.trace)


def test_web_denial_variants_corrected():
    for deny in ("不能上网", "无法执行联网检索", "没有联网能力", "无法访问网络"):
        s = state_with_web(2)
        fixed = LLMAnswerGenerator._fix_web_honesty(f"抱歉，我{deny}。", s)
        assert deny not in fixed, f"{deny} 未被纠正"


def test_no_web_execution_does_not_touch_answer():
    s = state_with_web(0, web_executed=False)
    answer = "我无法联网检索。"
    assert LLMAnswerGenerator._fix_web_honesty(answer, s) == answer
    assert not any(e.event == "web_honesty_fix" for e in s.trace)


def test_zero_results_does_not_touch_answer():
    """count=0 由 #76 阻断，不在这里改。"""
    s = state_with_web(0)
    answer = "我无法联网检索。"
    assert LLMAnswerGenerator._fix_web_honesty(answer, s) == answer


def test_honest_answer_unchanged():
    s = state_with_web(3)
    answer = "本轮搜索返回的 3 条结果与问题无关，未找到可引用的网页证据。"
    assert LLMAnswerGenerator._fix_web_honesty(answer, s) == answer
    assert not any(e.event == "web_honesty_fix" for e in s.trace)
