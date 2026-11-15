"""MemoryManager 的测试。

重点守三道闸的边界：
  1. 没有强信号时不消耗模型调用（RQ4 的成本）；
  2. 敏感信息绝不入库（Proposal 4.3 的"安全"条件）；
  3. 重复记忆不再写（否则记忆库被噪音淹没，RQ2 命中率下降）。
"""

from __future__ import annotations

import pytest

from app.agent.factory import build_orchestrator, build_stage1_orchestrator
from app.core.interfaces import MemoryManager
from app.core.mocks import (
    MockAnswerGenerator,
    MockLLM,
    MockMemoryStore,
    MockPlanner,
    MockRetriever,
    MockVerifier,
)
from app.core.types import (
    LLMResponse,
    MemoryItem,
    MemoryTier,
    Usage,
)
from app.memory import LLMMemoryManager, find_sensitive, looks_memorable
from app.memory.manager import _is_duplicate, _normalise_content
from app.routing.router import RuleBasedRouter

VALID = """[
  {"tier": "preference", "content": "用户偏好中文回答，并要求结论附页码引用",
   "importance": 0.8, "reason": "长期稳定的表达偏好"},
  {"tier": "profile", "content": "用户是香港大学 COMP7608B 的学生",
   "importance": 0.9, "reason": "身份背景"}
]"""


class FakeLLM:
    def __init__(self, text: str = VALID, *, error: Exception | None = None,
                 total_tokens: int = 64) -> None:
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
            usage=Usage(total_tokens=self.total_tokens, llm_calls=1, latency_ms=9.0),
        )


def manager(text: str = VALID, **kwargs) -> tuple[LLMMemoryManager, FakeLLM]:
    llm = FakeLLM(text, **kwargs)
    return LLMMemoryManager(llm), llm


# --------------------------------------------------------------------------- #
# 接口一致性
# --------------------------------------------------------------------------- #
def test_satisfies_memory_manager_protocol():
    mgr, _ = manager()
    assert isinstance(mgr, MemoryManager)


# --------------------------------------------------------------------------- #
# 闸 1：关键词预筛
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "question",
    ["帮我总结这段", "今天天气不错", "什么是 attention", "改一下格式", "嗯", ""],
)
def test_no_signal_skips_llm_call(question):
    """没有记忆信号时不该花一次模型调用。"""
    mgr, llm = manager()
    got = mgr.extract_candidates(question, "好的")

    assert got == []
    assert llm.calls == 0
    assert mgr.last_stats["skipped"] == "no_signal"


@pytest.mark.parametrize(
    "question",
    ["我偏好中文回答", "我在做 RAG 综述", "以后都用表格呈现", "记住我的研究方向是 Agent"],
)
def test_signal_triggers_llm_call(question):
    mgr, llm = manager()
    mgr.extract_candidates(question, "好的")
    assert llm.calls == 1


def test_looks_memorable_is_case_insensitive_for_english():
    assert looks_memorable("I prefer Chinese answers")
    assert not looks_memorable("summarize this")


# --------------------------------------------------------------------------- #
# 正常抽取
# --------------------------------------------------------------------------- #
def test_extracts_valid_candidates():
    mgr, llm = manager()
    got = mgr.extract_candidates("我偏好中文回答并要页码", "好的")

    assert len(got) == 2
    assert got[0].tier == MemoryTier.PREFERENCE
    assert got[0].importance == pytest.approx(0.8)
    assert got[1].tier == MemoryTier.PROFILE
    assert all(c.reason for c in got), "reason 要写进实验日志，不能为空"
    assert llm.last_json_mode is True


def test_parses_fenced_json():
    mgr, _ = manager(f"好的：\n```json\n{VALID}\n```")
    assert len(mgr.extract_candidates("我偏好中文", "ok")) == 2


def test_existing_memories_reach_prompt():
    mgr, llm = manager()
    existing = [
        MemoryItem(memory_id="m1", tier=MemoryTier.PROFILE, content="用户是港大学生")
    ]
    mgr.extract_candidates("我是港大的", "ok", existing)
    assert "用户是港大学生" in llm.prompts[0]


# --------------------------------------------------------------------------- #
# 闸 3：敏感信息
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "content",
    [
        "用户的手机号是 13812345678",
        "用户的邮箱是 mumu@connect.hku.hk",
        "用户的身份证号 110101199001011234",
        "用户的学号是 3036791588",
        "用户的密码是 abc123",
        "用户的 API key 是 sk-abcdefghijklmnop",
    ],
)
def test_sensitive_content_is_dropped(content):
    payload = f'[{{"tier": "profile", "content": "{content}", "importance": 0.9}}]'
    mgr, _ = manager(payload)
    got = mgr.extract_candidates("我是港大的", "ok")

    assert got == [], "含敏感信息的记忆必须丢弃"
    assert mgr.last_stats["dropped_sensitive"] == 1


def test_find_sensitive_reports_type():
    assert find_sensitive("电话 13812345678") == "手机号"
    assert find_sensitive("a@b.com") == "邮箱"
    assert find_sensitive("用户的密码是 123") == "密码"
    assert find_sensitive("用户偏好中文回答") is None


# --------------------------------------------------------------------------- #
# 闸 3：去重
# --------------------------------------------------------------------------- #
def test_duplicate_against_existing_is_dropped():
    payload = '[{"tier": "preference", "content": "用户偏好中文回答，并要求附页码", "importance": 0.8}]'
    mgr, _ = manager(payload)
    existing = [
        MemoryItem(
            memory_id="m1",
            tier=MemoryTier.PREFERENCE,
            content="用户偏好中文回答，并要求附页码",
        )
    ]
    got = mgr.extract_candidates("我偏好中文回答", "ok", existing)

    assert got == []
    assert mgr.last_stats["dropped_duplicate"] == 1


def test_duplicate_inside_batch_is_dropped():
    payload = """[
      {"tier": "preference", "content": "用户偏好中文回答并附页码", "importance": 0.8},
      {"tier": "preference", "content": "用户偏好中文回答并附页码", "importance": 0.7}
    ]"""
    mgr, _ = manager(payload)
    got = mgr.extract_candidates("我偏好中文回答", "ok")

    assert len(got) == 1, "同一批里重复的只留一条"


def test_is_duplicate_threshold():
    assert _is_duplicate("用户偏好中文回答", ["用户偏好中文回答"])
    assert not _is_duplicate("用户偏好中文回答", ["用户是港大学生"])


# --------------------------------------------------------------------------- #
# 闸 3：格式清洗
# --------------------------------------------------------------------------- #
def test_invalid_tier_is_dropped():
    payload = '[{"tier": "mood", "content": "用户今天心情不错", "importance": 0.5}]'
    mgr, _ = manager(payload)
    # 问题里必须带记忆信号，否则会先被闸 1 拦掉，走不到解析
    assert mgr.extract_candidates("我是港大的学生", "ok") == []
    assert mgr.last_stats["dropped_invalid"] == 1


def test_too_short_content_is_dropped():
    payload = '[{"tier": "preference", "content": "中文", "importance": 0.5}]'
    mgr, _ = manager(payload)
    assert mgr.extract_candidates("我偏好中文", "ok") == []


def test_citation_markers_are_stripped():
    payload = '[{"tier": "task_state", "content": "用户正在做 RAG 综述 [1][2]", "importance": 0.7}]'
    mgr, _ = manager(payload)
    got = mgr.extract_candidates("我正在做 RAG 综述", "ok")

    assert got and "[1]" not in got[0].content


def test_long_content_is_truncated():
    long_text = "用户" + "很" * 300
    payload = f'[{{"tier": "profile", "content": "{long_text}", "importance": 0.5}}]'
    mgr, _ = manager(payload)
    got = mgr.extract_candidates("我是学生", "ok")

    assert got and got[0].content.endswith("…")
    assert len(got[0].content) <= 120


def test_importance_is_clamped():
    payload = """[
      {"tier": "profile", "content": "用户是港大 COMP7608B 学生", "importance": 9.9},
      {"tier": "profile", "content": "用户研究方向是检索增强生成", "importance": -3}
    ]"""
    mgr, _ = manager(payload)
    got = mgr.extract_candidates("我是港大的", "ok")

    assert got[0].importance == 1.0
    assert got[1].importance == 0.0


def test_normalise_content_rejects_non_string():
    assert _normalise_content(None) is None
    assert _normalise_content(123) is None
    assert _normalise_content("   ") is None


def test_max_candidates_is_enforced():
    items = ",".join(
        f'{{"tier": "profile", "content": "用户特征编号{index}号描述", "importance": 0.5}}'
        for index in range(1, 8)
    )
    mgr, _ = manager(f"[{items}]")
    got = mgr.extract_candidates("我是学生", "ok", )

    assert len(got) == 3, "默认上限 3 条，避免一次写入过多"


# --------------------------------------------------------------------------- #
# 失败与降级
# --------------------------------------------------------------------------- #
def test_llm_failure_returns_empty():
    mgr, _ = manager(error=RuntimeError("timeout"))
    assert mgr.extract_candidates("我偏好中文", "ok") == []
    assert mgr.last_stats["skipped"].startswith("llm_error")


def test_parse_failure_returns_empty():
    mgr, _ = manager("我判断不出来")
    assert mgr.extract_candidates("我偏好中文", "ok") == []
    assert mgr.last_stats["skipped"] == "parse_error"


def test_empty_array_returns_empty():
    mgr, _ = manager("[]")
    assert mgr.extract_candidates("我偏好中文", "ok") == []
    assert mgr.last_stats["proposed"] == 0


def test_last_usage_records_cost():
    mgr, _ = manager(total_tokens=88)
    mgr.extract_candidates("我偏好中文", "ok")
    assert mgr.last_usage.total_tokens == 88


def test_last_usage_reset_when_skipped():
    mgr, _ = manager(total_tokens=88)
    mgr.extract_candidates("今天天气不错", "ok")
    assert mgr.last_usage.total_tokens == 0


def test_stats_funnel_is_consistent():
    payload = """[
      {"tier": "preference", "content": "用户偏好中文回答并附页码", "importance": 0.8},
      {"tier": "profile", "content": "用户手机号 13812345678", "importance": 0.9},
      {"tier": "mood", "content": "用户今天心情很好", "importance": 0.5}
    ]"""
    mgr, _ = manager(payload)
    mgr.extract_candidates("我偏好中文回答", "ok")
    stats = mgr.last_stats

    assert stats["proposed"] == 3
    assert stats["accepted"] == 1
    assert stats["dropped_sensitive"] == 1
    assert stats["dropped_invalid"] == 1


# --------------------------------------------------------------------------- #
# 参数校验
# --------------------------------------------------------------------------- #
def test_rejects_invalid_max_candidates():
    with pytest.raises(ValueError):
        LLMMemoryManager(FakeLLM(), max_candidates=0)


def test_rejects_invalid_threshold():
    with pytest.raises(ValueError):
        LLMMemoryManager(FakeLLM(), duplicate_threshold=1.5)


# --------------------------------------------------------------------------- #
# 集成：工厂与编排器
# --------------------------------------------------------------------------- #
def test_stage1_orchestrator_uses_llm_memory_manager():
    orch = build_stage1_orchestrator()
    assert isinstance(orch.memory_manager, LLMMemoryManager)
    # 存储层仍为 Mock，等成员 B 交付 MemoryStore
    assert type(orch.memory_store).__name__ == "MockMemoryStore"


def test_orchestrator_logs_memory_funnel():
    """记忆漏斗必须进 trace，RQ2 要读它算写入拒绝率。"""
    mgr = LLMMemoryManager(FakeLLM(), max_candidates=1)
    orch = build_orchestrator(
        router=RuleBasedRouter(),
        planner=MockPlanner(),
        retriever=MockRetriever(),
        memory_store=MockMemoryStore(),
        memory_manager=mgr,
        verifier=MockVerifier(),
        answer_generator=MockAnswerGenerator(),
        llm=MockLLM(),
    )

    state = orch.run("我偏好中文回答并要页码", enable_memory=True)

    stats = [e for e in state.trace if e.stage == "memory_write" and e.event == "stats"]
    assert stats, "必须留下记忆漏斗事件"
    assert stats[0].detail["proposed"] == 2
    assert state.usage.total_tokens >= 64, "记忆抽取消耗的 token 也要计入"
