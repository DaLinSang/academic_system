"""Issue #32（A 部分）：会话持久化与恢复的契约测试。

守三件事：
  1. SessionStore 与 RunLogger 用途分离（聊天存储 vs 实验记录，目录不混）；
  2. 恢复历史会话后 turn_index 连续（验收标准：读取历史后序号不跳不重）；
  3. 清空一个会话不影响另一会话、不影响长期记忆存储。

这些是 #32 里 A 负责的"会话数据与 Agent 接入"部分。
用独立文件而非追加 test_session_store.py，避免与 C 的 UI PR（#59）冲突。
"""

from __future__ import annotations

import json
from pathlib import Path

from app.agent.session_store import SessionStore
from app.core.types import AgentState, ConversationTurn


def make_state(question: str, turn_index: int, user: str = "u1", session: str = "s1") -> AgentState:
    return AgentState(
        question=question,
        final_answer=f"回答：{question}",
        user_id=user,
        session_id=session,
        turn_index=turn_index,
    )


# --------------------------------------------------------------------------- #
# 1. SessionStore 与 RunLogger 目录边界
# --------------------------------------------------------------------------- #
def test_session_store_does_not_touch_runs_experiments(tmp_path: Path):
    """聊天存储写在 runs/sessions/，不与实验目录 runs/<run_id>/ 混。"""
    store = SessionStore(tmp_path / "sessions")
    store.append(make_state("q0", 0))

    written = list((tmp_path / "sessions").rglob("*.jsonl"))
    assert len(written) == 1
    # 文件按 user 分目录，不是实验的 meta.json / samples.jsonl 结构
    assert written[0].name == "s1.jsonl"
    assert written[0].parent.name == "u1"
    assert list((tmp_path / "sessions").rglob("meta.json")) == []


# --------------------------------------------------------------------------- #
# 2. turn_index 连续性（核心）
# --------------------------------------------------------------------------- #
def test_turn_index_is_contiguous_after_restore(tmp_path: Path):
    """3 轮落盘 → 恢复 → 下一轮索引应为 3，不跳号不重复。"""
    store = SessionStore(tmp_path)
    for i in range(3):
        store.append(make_state(f"问题{i}", i))

    restored = store.restore("u1", "s1")
    assert [s.turn_index for s in restored] == [0, 1, 2]

    # 模拟 UI 用恢复的 state 构造下一轮历史（Issue #44 的 ConversationTurn）
    turns = [
        ConversationTurn(question=s.question, answer=s.final_answer, turn_index=idx)
        for idx, s in enumerate(restored)
    ]
    next_index = len(turns)
    assert next_index == 3, "恢复后下一轮必须从 3 继续"

    # 追加第 4 轮后再次恢复，序号仍连续
    store.append(make_state("问题3", next_index))
    assert [s.turn_index for s in store.restore("u1", "s1")] == [0, 1, 2, 3]


def test_turn_index_restarts_after_clear(tmp_path: Path):
    """清空会话后重新写入，turn_index 从 0 重新开始。"""
    store = SessionStore(tmp_path)
    store.append(make_state("q0", 0))
    store.append(make_state("q1", 1))
    assert store.clear("u1", "s1") is True
    assert store.restore("u1", "s1") == []

    store.append(make_state("新会话第一轮", 0))
    assert [s.turn_index for s in store.restore("u1", "s1")] == [0]


def test_restore_preserves_turn_index_from_record_not_position(tmp_path: Path):
    """恢复读的是落盘的 turn_index，不是文件行号——乱序写入也能还原。"""
    store = SessionStore(tmp_path)
    for i in (0, 2, 1):  # 故意乱序写入
        store.append(make_state(f"问题{i}", i))
    # restore 从文件读取，turn_index 与写入顺序一致（这是记录的事实）
    assert [s.turn_index for s in store.restore("u1", "s1")] == [0, 2, 1]


# --------------------------------------------------------------------------- #
# 3. 会话隔离（验收：清空一个不影响另一个）
# --------------------------------------------------------------------------- #
def test_clear_one_session_keeps_another(tmp_path: Path):
    store = SessionStore(tmp_path)
    store.append(make_state("会话A", 0, session="sA"))
    store.append(make_state("会话B", 0, session="sB"))

    assert store.clear("u1", "sA") is True
    assert store.restore("u1", "sA") == []
    assert [s.question for s in store.restore("u1", "sB")] == ["会话B"]


def test_different_users_sessions_are_isolated(tmp_path: Path):
    store = SessionStore(tmp_path)
    store.append(make_state("用户A的问题", 0, user="uA"))
    store.append(make_state("用户B的问题", 0, user="uB"))

    assert [s.question for s in store.restore("uA", "s1")] == ["用户A的问题"]
    assert [s.question for s in store.restore("uB", "s1")] == ["用户B的问题"]


# --------------------------------------------------------------------------- #
# 4. 敏感信息不进聊天记录（验收第 5 点）
# --------------------------------------------------------------------------- #
def test_api_key_not_written_to_chat_history(tmp_path: Path):
    store = SessionStore(tmp_path)
    state = make_state("我的 key 是 sk-abc123456789", 0)
    store.append(state)

    raw = (tmp_path / "u1" / "s1.jsonl").read_text(encoding="utf-8")
    assert "sk-abc123456789" not in raw
    assert "[已脱敏]" in raw
    # 仍是合法 JSONL
    assert json.loads(raw.splitlines()[0])["question"]
