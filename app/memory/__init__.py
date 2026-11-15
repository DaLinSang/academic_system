"""长期记忆模块。

三个文件，两个职责层，负责人不同：

    manager.py         MemoryManager —— LLM 策略层（成员 A，主策略）
    policy.py          MemoryManager —— 规则版（成员 B，作消融基线）
    postgres_store.py  MemoryStore   —— PostgreSQL 存储层（成员 B）

三者通过 `app.core.types.MemoryCandidate` 交接：
MemoryManager 产出候选，MemoryStore 消费候选并落成 `MemoryItem`。

两套 MemoryManager 实现同一接口，用于对比「规则 vs 模型」对 RQ2 的影响。
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
