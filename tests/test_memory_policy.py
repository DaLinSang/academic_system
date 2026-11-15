"""Check conservative candidate extraction and duplicate suppression."""

from app.core.interfaces import MemoryManager
from app.core.types import MemoryItem, MemoryTier
from app.memory import RuleBasedMemoryManager


def test_explicit_preferences_profile_and_task_state():
    manager = RuleBasedMemoryManager()
    assert isinstance(manager, MemoryManager)
    found = manager.extract_candidates(
        "请用中文回答。我在读计算机硕士。我正在写 RAG 报告。",
        "助手生成的内容不应保存",
    )
    assert {candidate.tier for candidate in found} == {
        MemoryTier.PREFERENCE,
        MemoryTier.PROFILE,
        MemoryTier.TASK_STATE,
    }


def test_no_candidate_for_generic_question_or_sensitive_content():
    manager = RuleBasedMemoryManager()
    assert manager.extract_candidates("什么是 RAG？", "我是助手编造的事实") == []
    assert manager.extract_candidates("我的专业是计算机，邮箱是 me@example.com", "") == []
    assert manager.extract_candidates("请用我的 API_KEY 回答", "") == []
    assert manager.extract_candidates("请用中文解释 RAG", "") == []


def test_existing_memory_is_not_repeated():
    manager = RuleBasedMemoryManager()
    existing = MemoryItem(
        memory_id="m1", tier=MemoryTier.PREFERENCE, content="请用中文回答"
    )
    assert manager.extract_candidates("请用中文回答。", "", [existing]) == []
