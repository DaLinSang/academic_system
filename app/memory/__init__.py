"""长期记忆模块。

三个文件，两个职责层，负责人不同：

    manager.py         MemoryManager —— LLM 策略层（成员 A）  ✅ 运行时默认
    policy.py          MemoryManager —— 规则版（成员 B）      ⬜ 保留但不启用
    postgres_store.py  MemoryStore   —— PostgreSQL 存储层（成员 B）✅ 运行时启用

三者通过 `app.core.types.MemoryCandidate` 交接：
MemoryManager 产出候选，MemoryStore 消费候选并落成 `MemoryItem`。

`policy.py` 的规则版只用于 RQ2 的「规则 vs 模型」消融对照，
切换方式：`build_stage1_orchestrator(memory_strategy="rule")`。
"""

from app.memory.manager import LLMMemoryManager, find_sensitive, looks_memorable
from app.memory.policy import RuleBasedMemoryManager
from app.memory.postgres_store import OllamaMemoryEmbedder, PostgresMemoryStore

__all__ = [
    "LLMMemoryManager",
    "OllamaMemoryEmbedder",
    "PostgresMemoryStore",
    "RuleBasedMemoryManager",
    "find_sensitive",
    "looks_memorable",
]
