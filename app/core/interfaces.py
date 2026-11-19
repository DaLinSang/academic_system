"""模块接口（抽象层）。

**这是三人协作的核心文件。** 本文件只声明签名，不含任何实现；
方法体里的 `...` 是 Python 的空方法体占位符，等价于 Java 接口方法后面的 `;`。

谁实现什么（截至 2026-11-16）：

    Protocol          实现类                     位置                            负责人  运行时
    ----------------  -------------------------  ------------------------------  ------  ------
    Retriever         HybridRetriever            app/retrieval/                  B      ✅
    MemoryStore       PostgresMemoryStore        app/memory/postgres_store.py    B      ✅
    MemoryManager     LLMMemoryManager           app/memory/manager.py           A      ✅ 主策略
                      RuleBasedMemoryManager     app/memory/policy.py            B      ⬜ 基线保留未启用
    Verifier          LexicalVerifier            app/verification/               C      ✅
    Router            LLMRouter                  app/routing/                    A      ✅
    Planner           LLMPlanner                 app/planning/                   A      ✅
    LLM               OpenAICompatibleLLM        app/llm/client.py               A      ✅
    AnswerGenerator   LLMAnswerGenerator         app/agent/answer_generator.py   A      ✅

`MemoryManager` 是唯一有两个实现的接口，按分工：
  - **策略层（判断要不要存、存什么）归 A** → `LLMMemoryManager`，运行时启用
  - **存储层（怎么存、怎么查）归 B** → `PostgresMemoryStore`
  - `RuleBasedMemoryManager` 是 B 早期垫的规则版，保留作 RQ2 的消融基线，**默认不启用**。
    切换方式：`build_stage1_orchestrator(memory_strategy="rule")`。

Python 不写 `implements`：只要方法名与签名对得上就自动满足接口（结构化类型）。
`@runtime_checkable` 让它支持 `isinstance()`，测试里靠这个发现实现漂移。

任何一方只要遵守签名，Mock 和真实实现就能随时互换，
`Orchestrator` 的代码一行都不用改。

签名变更规则：必须三人确认 + 同步更新本文件 + 通知受影响的 Mock 与实现。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.core.types import (
    AgentState,
    ClaimCheck,
    ConversationTurn,
    Evidence,
    LLMResponse,
    MemoryCandidate,
    MemoryItem,
    MemoryTier,
    PlanStep,
    RouteDecision,
)

__all__ = [
    "Retriever",
    "MemoryStore",
    "MemoryManager",
    "Verifier",
    "Router",
    "Planner",
    "LLM",
    "AnswerGenerator",
]


# --------------------------------------------------------------------------- #
# B 负责实现
# --------------------------------------------------------------------------- #
@runtime_checkable
class Retriever(Protocol):
    """文档检索。B 的交付物。

    实现要求：
      - 返回的 Evidence 必须带 file_name 和 page_number（页码不能丢）；
      - 没有命中时返回空列表，不要抛异常；
      - 数据库不可用时抛 RetrievalError，由 Orchestrator 记录并降级。
    """

    def retrieve(self, query: str, top_k: int = 5) -> list[Evidence]:
        ...

    def index_document(self, pdf_path: str, document_id: str | None = None) -> int:
        """导入一篇 PDF，返回切分出的 chunk 数量。"""
        ...


@runtime_checkable
class MemoryStore(Protocol):
    """长期记忆的存取。B 的交付物，内部用 PostgreSQL 由 B 决定。

    其他模块**不得**直接操作数据库，只能通过这五个方法。
    """

    def add(self, candidate: MemoryCandidate, user_id: str = "default") -> MemoryItem:
        ...

    def search(
        self,
        query: str,
        user_id: str = "default",
        tiers: list[MemoryTier] | None = None,
        top_k: int = 5,
    ) -> list[MemoryItem]:
        ...

    def update(
        self,
        memory_id: str,
        content: str | None = None,
        importance: float | None = None,
        *,
        user_id: str = "default",
    ) -> MemoryItem | None:
        ...

    def delete(self, memory_id: str, *, user_id: str = "default") -> bool:
        ...

    def list_all(self, user_id: str = "default") -> list[MemoryItem]:
        ...


# --------------------------------------------------------------------------- #
# A 负责实现（策略层；存储层 MemoryStore 归 B）
# --------------------------------------------------------------------------- #
@runtime_checkable
class MemoryManager(Protocol):
    """决定"这段对话里有没有值得长期保存的信息"。

    与 MemoryStore 的分工：
      MemoryManager（A）= 要不要存、存什么  →  产出 MemoryCandidate
      MemoryStore（B）  = 怎么存、怎么查     →  消费 MemoryCandidate

    两套实现：
      LLMMemoryManager（A，app/memory/manager.py）—— 运行时启用
      RuleBasedMemoryManager（B，app/memory/policy.py）—— 基线保留，默认不启用

    Proposal 4.3 的写入门槛：稳定 + 对未来有用 + 不敏感。原始对话不存。
    """

    def extract_candidates(
        self,
        question: str,
        answer: str,
        existing_memories: list[MemoryItem] | None = None,
    ) -> list[MemoryCandidate]:
        ...


@runtime_checkable
class Router(Protocol):
    """A 的交付物。第一版只分 direct / document_retrieval / complex_task。"""

    def route(
        self,
        question: str,
        has_documents: bool = True,
        memory_context: list[MemoryItem] | None = None,
        history: list[ConversationTurn] | None = None,
    ) -> RouteDecision:
        # history 是可选扩展（Issue #44）。不传时行为与扩展前一致。
        ...


@runtime_checkable
class Planner(Protocol):
    """A 的交付物。只生成步骤，不执行工具。"""

    def plan(
        self,
        question: str,
        route: RouteDecision,
        memory_context: list[MemoryItem] | None = None,
        history: list[ConversationTurn] | None = None,
    ) -> list[PlanStep]:
        # history 是可选扩展（Issue #44）。不传时行为与扩展前一致。
        ...


@runtime_checkable
class LLM(Protocol):
    """A 的交付物，全系统唯一的模型出口。

    禁止任何模块直接 import openai 或直接读 API key。
    """

    def generate(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        ...


@runtime_checkable
class AnswerGenerator(Protocol):
    """A 的交付物：根据 Evidence 生成带 [n] 引用的回答。"""

    def generate(self, state: AgentState) -> str:
        ...


# --------------------------------------------------------------------------- #
# C 负责实现
# --------------------------------------------------------------------------- #
@runtime_checkable
class Verifier(Protocol):
    """C 的交付物：把答案拆成 claim，逐条对证据校验。

    实现要求：
      - evidence 为空时，所有 claim 判 unsupported，不要抛异常；
      - 每条 ClaimCheck.supporting_evidence 必须是 state.evidence 里真实存在的 chunk_id；
      - 校验失败时返回降级结果（全部 unsupported + note），让 Agent 仍能出答案。
    """

    def verify(self, answer: str, evidence: list[Evidence]) -> list[ClaimCheck]:
        ...
