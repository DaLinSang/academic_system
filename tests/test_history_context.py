"""历史上下文格式化的单元测试（Issue #44）。"""

from __future__ import annotations

from datetime import timedelta

from app.agent.history_context import (
    DEFAULT_MAX_TOTAL_CHARS,
    format_history,
)
from app.core.types import ConversationTurn


def turn(index: int, question: str, answer: str) -> ConversationTurn:
    return ConversationTurn(
        question=question,
        answer=answer,
        turn_index=index,
        started_at=ConversationTurn.model_fields["started_at"].default_factory()
        + timedelta(seconds=index),
    )


def test_empty_history_returns_empty():
    assert format_history(None) == ""
    assert format_history([]) == ""


def test_single_turn_contains_both_question_and_answer():
    text = format_history([turn(0, "我在做 RAG 综述", "好的，请说说具体方向")])

    assert "我在做 RAG 综述" in text
    assert "好的，请说说具体方向" in text
    assert "历史对话" in text


def test_turns_are_ordered_oldest_first():
    text = format_history(
        [
            turn(2, "问题二", "回答二"),
            turn(0, "问题零", "回答零"),
            turn(1, "问题一", "回答一"),
        ]
    )

    assert text.index("问题零") < text.index("问题一") < text.index("问题二")


def test_only_recent_n_turns_kept():
    history = [turn(i, f"问题{i}", f"回答{i}") for i in range(8)]
    text = format_history(history, max_turns=3)

    assert "问题0" not in text
    assert "问题4" not in text
    assert "问题5" in text
    assert "问题7" in text


def test_long_answer_is_clipped():
    text = format_history(
        [turn(0, "问题", "长" * 1000)],
        max_chars_per_turn=100,
    )

    assert "…" in text
    assert len(text) < 200


def test_total_budget_is_hard_cap():
    history = [turn(i, f"问题{i}" * 50, f"回答{i}" * 50) for i in range(10)]
    text = format_history(history, max_total_chars=300, max_chars_per_turn=200)

    assert len(text) <= 300


def test_zero_budget_disables_history():
    assert format_history([turn(0, "q", "a")], max_total_chars=0) == ""
    assert format_history([turn(0, "q", "a")], max_turns=0) == ""


def test_default_budget_is_reasonable():
    history = [turn(i, f"问题{i}" * 100, f"回答{i}" * 300) for i in range(20)]
    text = format_history(history)

    assert len(text) <= DEFAULT_MAX_TOTAL_CHARS


def test_whitespace_is_collapsed():
    text = format_history([turn(0, "问题  很多\n空格", "回答\t制表")])

    assert "问题 很多 空格" in text
    assert "回答 制表" in text
