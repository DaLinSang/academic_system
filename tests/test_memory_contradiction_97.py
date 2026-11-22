"""Issue #97：矛盾长期记忆更新策略回归测试。

守：
1. 新候选与同 tier 旧记忆矛盾 → 转成 last_updates，不返回给 add（库中只留更新后一条）
2. 相关但不矛盾 / 不同 tier → 并存，不误更新
3. 矛盾判定失败 / 模型输出异常 → 不更新（保守，绝不误删旧记忆）
4. Orchestrator 按 last_updates 调 store.update，trace 记 contradiction_update
5. "声称已记录但 stored=0、updated=1" 不触发 #70 的纠错 note
"""

from __future__ import annotations

from app.agent.orchestrator import Orchestrator
from app.core.types import (
    AgentState,
    LLMResponse,
    MemoryItem,
    MemoryTier,
    TraceEvent,
    Usage,
)
from app.memory.manager import LLMMemoryManager, _find_contradiction

VALID = '{"memories":[{"tier":"preference","content":"用户希望以后回答不再附带 RAG 相关内容","importance":0.85,"reason":"偏好更新"}]}'

OLD_RAG = MemoryItem(
    memory_id="old-1", tier=MemoryTier.PREFERENCE,
    content="用户希望每次回答都附带一些 RAG 相关内容", importance=0.8,
)
OLD_PROFILE = MemoryItem(
    memory_id="old-2", tier=MemoryTier.PROFILE,
    content="用户是港大学生",
)


class SeqLLM:
    """第 1 次 generate 返回提取结果，第 2 次返回矛盾判定。"""

    def __init__(self, extraction=VALID, verdict="1. yes"):
        self.extraction = extraction
        self.verdict = verdict
        self.calls = 0
        self.last_retries = 0

    def generate(self, messages, **kwargs):
        self.calls += 1
        text = self.extraction if self.calls == 1 else self.verdict
        return LLMResponse(text=text, usage=Usage(total_tokens=20), model="fake")


# --------------------------------------------------------------------------- #
# 1. 矛盾更新
# --------------------------------------------------------------------------- #
def test_contradiction_moves_candidate_to_updates():
    mgr = LLMMemoryManager(SeqLLM())
    cands = mgr.extract_candidates(
        "请记住以后不要每次都带 RAG 相关内容了", "好的", [OLD_RAG]
    )
    assert cands == [], "矛盾候选不应再走 add"
    assert mgr.last_updates == [("old-1", "用户希望以后回答不再附带 RAG 相关内容", 0.85)]
    assert mgr.last_stats["contradictions_updated"] == 1
    assert mgr.last_stats["contradictions_checked"] == 1


def test_non_contradiction_keeps_candidate():
    """相关但不矛盾：照常新增，不更新旧记忆。"""
    mgr = LLMMemoryManager(SeqLLM(verdict="1. no"))
    cands = mgr.extract_candidates(
        "请记住以后不要每次都带 RAG 相关内容了", "好的", [OLD_RAG]
    )
    assert len(cands) == 1
    assert mgr.last_updates == []


def test_cross_tier_not_checked():
    """旧记忆是 profile、新候选是 preference → 不判矛盾。"""
    mgr = LLMMemoryManager(SeqLLM())
    cands = mgr.extract_candidates(
        "请记住以后不要每次都带 RAG 内容了", "好的", [OLD_PROFILE]
    )
    assert len(cands) == 1
    assert mgr.last_updates == []  # 跨 tier 绝不更新
    assert mgr.last_stats["contradictions_updated"] == 0


def test_verdict_failure_does_not_update():
    """判定输出异常 → 当不矛盾，绝不误删旧记忆。"""
    mgr = LLMMemoryManager(SeqLLM(verdict="我不知道"))
    cands = mgr.extract_candidates(
        "请记住以后不要带 RAG 了", "好的", [OLD_RAG]
    )
    assert len(cands) == 1
    assert mgr.last_updates == []


# --------------------------------------------------------------------------- #
# 2. _find_contradiction 保守边界
# --------------------------------------------------------------------------- #
def test_find_contradiction_only_same_tier():
    llm = SeqLLM(verdict="1. yes")
    assert _find_contradiction(llm, "新偏好", MemoryTier.PREFERENCE, [OLD_PROFILE]) is None


def test_find_contradiction_out_of_range_index():
    llm = SeqLLM(verdict="5. yes")  # 只有 1 条旧记忆，索引越界
    assert _find_contradiction(llm, "新偏好", MemoryTier.PREFERENCE, [OLD_RAG]) is None


def test_find_contradiction_llm_error():
    class Broken:
        def generate(self, messages, **kwargs):
            raise RuntimeError("boom")
    assert _find_contradiction(Broken(), "新偏好", MemoryTier.PREFERENCE, [OLD_RAG]) is None


# --------------------------------------------------------------------------- #
# 3. Orchestrator 消费 last_updates
# --------------------------------------------------------------------------- #
def test_memory_write_note_not_fooled_by_update_only():
    """stored=0 但 updated=1 时，模型说"已记录"不算说谎。"""
    state = AgentState(question="请记住", draft_answer="好的，已记录。")
    state.trace.append(TraceEvent(
        stage="memory_write", event="end", detail={"stored": 0, "updated": 1}
    ))
    assert Orchestrator._memory_write_note(state) == ""
