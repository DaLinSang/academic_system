"""记忆策略的运行时装配。

这个文件只回答一个问题：**默认跑起来时，执行的是哪个文件的代码？**

`MemoryManager` 有两套实现（A 的 LLM 版、B 的规则版），文档里说清楚不够，
必须用测试钉死，否则某次重构把默认值改回去也没人发现。
"""

from __future__ import annotations

import pytest

from app.agent.factory import build_mock_orchestrator, build_stage1_orchestrator
from app.core.interfaces import MemoryManager
from app.core.mocks import MockMemoryManager
from app.memory import LLMMemoryManager, RuleBasedMemoryManager


# --------------------------------------------------------------------------- #
# 默认值：必须走 A 的 LLM 策略
# --------------------------------------------------------------------------- #
def test_default_strategy_is_llm():
    orch = build_stage1_orchestrator()
    assert isinstance(orch.memory_manager, LLMMemoryManager)


def test_default_runs_code_from_manager_py():
    """钉死文件路径：默认执行的必须是 app/memory/manager.py。"""
    orch = build_stage1_orchestrator()
    assert type(orch.memory_manager).__module__ == "app.memory.manager"
    assert type(orch.memory_manager).__name__ == "LLMMemoryManager"


def test_default_does_not_use_rule_based():
    orch = build_stage1_orchestrator()
    assert not isinstance(orch.memory_manager, RuleBasedMemoryManager)


# --------------------------------------------------------------------------- #
# 显式切换：只有写了 memory_strategy="rule" 才走 policy.py
# --------------------------------------------------------------------------- #
def test_rule_strategy_is_opt_in():
    orch = build_stage1_orchestrator(memory_strategy="rule")
    assert isinstance(orch.memory_manager, RuleBasedMemoryManager)


def test_rule_strategy_runs_code_from_policy_py():
    orch = build_stage1_orchestrator(memory_strategy="rule")
    assert type(orch.memory_manager).__module__ == "app.memory.policy"
    assert type(orch.memory_manager).__name__ == "RuleBasedMemoryManager"


def test_unknown_strategy_is_rejected():
    with pytest.raises(ValueError, match="memory_strategy"):
        build_stage1_orchestrator(memory_strategy="magic")


# --------------------------------------------------------------------------- #
# 其他装配不受影响
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("strategy", ["llm", "rule"])
def test_storage_layer_is_always_postgres(strategy):
    from app.memory import PostgresMemoryStore

    orch = build_stage1_orchestrator(memory_strategy=strategy)
    assert isinstance(orch.memory_store, PostgresMemoryStore), (
        "记忆策略切换不该影响存储层"
    )


def test_mock_orchestrator_still_uses_mock_manager():
    """全 Mock 入口不需要数据库，也不该被真实策略污染。"""
    orch = build_mock_orchestrator()
    assert isinstance(orch.memory_manager, MockMemoryManager)


# --------------------------------------------------------------------------- #
# 两套实现都满足接口
# --------------------------------------------------------------------------- #
class _StubLLM:
    """只用于构造 LLMMemoryManager，不参与实际调用。"""

    def generate(self, messages, temperature=0.0, max_tokens=None, json_mode=False):
        raise AssertionError("本测试不应触发模型调用")


@pytest.mark.parametrize(
    "manager",
    [LLMMemoryManager(_StubLLM()), RuleBasedMemoryManager()],
)
def test_both_implementations_satisfy_protocol(manager):
    assert isinstance(manager, MemoryManager)


def test_both_implementations_are_distinct_classes():
    assert LLMMemoryManager is not RuleBasedMemoryManager
    assert LLMMemoryManager.__module__ != RuleBasedMemoryManager.__module__
