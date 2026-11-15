"""装配工厂。

阶段 0：全部用 Mock 装配，`build_mock_orchestrator()` 一行就能跑通。
阶段 1 之后：B / C 的模块完成后，把对应参数换成真实实现即可，
**Orchestrator 和 demo 代码都不用改**。
"""

from __future__ import annotations

from app.agent.orchestrator import Orchestrator
from app.core.interfaces import (
    LLM,
    AnswerGenerator,
    MemoryManager,
    MemoryStore,
    Planner,
    Retriever,
    Router,
    Verifier,
)
from app.core.mocks import (
    MockAnswerGenerator,
    MockLLM,
    MockMemoryManager,
    MockMemoryStore,
    MockPlanner,
    MockRetriever,
    MockRouter,
    MockVerifier,
)

__all__ = ["build_mock_orchestrator", "build_orchestrator", "build_stage1_orchestrator"]


def build_orchestrator(
    *,
    router: Router,
    planner: Planner,
    retriever: Retriever,
    memory_store: MemoryStore,
    memory_manager: MemoryManager,
    verifier: Verifier,
    answer_generator: AnswerGenerator,
    llm: LLM,
    top_k: int = 5,
) -> Orchestrator:
    return Orchestrator(
        router=router,
        planner=planner,
        retriever=retriever,
        memory_store=memory_store,
        memory_manager=memory_manager,
        verifier=verifier,
        answer_generator=answer_generator,
        llm=llm,
        top_k=top_k,
    )


def build_mock_orchestrator(top_k: int = 5) -> Orchestrator:
    """阶段 0 的默认装配：全部 Mock，无需 API key、无需数据库。"""
    llm = MockLLM()
    return build_orchestrator(
        router=MockRouter(),
        planner=MockPlanner(),
        retriever=MockRetriever(),
        memory_store=MockMemoryStore(),
        memory_manager=MockMemoryManager(),
        verifier=MockVerifier(),
        answer_generator=MockAnswerGenerator(llm),
        llm=llm,
        top_k=top_k,
    )


def build_stage1_orchestrator(top_k: int = 5) -> Orchestrator:
    """阶段 1 的真实装配：能跑真模型的链路。

    真实：Router（LLM）、Planner（LLM）、Retriever（B 的混合检索）、
          MemoryStore（B 的 PostgreSQL + 向量检索）、
          Verifier（C 的词法校验）、AnswerGenerator（LLM）、
          MemoryManager（A 的 LLM 策略）

    记忆抽取有两套实现，同一接口，可按需切换：
      - LLMMemoryManager（默认）：LLM 判断稳定性与有用性
      - RuleBasedMemoryManager：纯规则，零模型成本，作为消融基线
    对比两者对 RQ2（长期记忆是否提升个性化与跨会话完成度）的影响时，
    只需改下面 memory_manager 那一行。

    前置条件：
      - `.env` 里配好 API key，否则 `get_llm()` 会退回 `MockLLM`，
        路由与规划会自动走关键词降级，链路仍能跑但质量等同 Mock
      - pgvector 已启动，且已用 `scripts/index_pdfs.py` 导入文档，
        否则检索返回空、回答会明确告知"资料不足"

    这里用函数内延迟导入：`build_mock_orchestrator()` 是阶段 0 的入口，
    不应该因为 psycopg / httpx 之类的依赖而变得不可用。
    """
    from app.agent.answer_generator import LLMAnswerGenerator
    from app.llm.client import get_llm
    from app.memory import LLMMemoryManager, PostgresMemoryStore
    from app.planning.planner import LLMPlanner
    from app.retrieval import HybridRetriever
    from app.routing.router import LLMRouter
    from app.verification import LexicalVerifier

    llm = get_llm()
    return build_orchestrator(
        router=LLMRouter(llm),
        planner=LLMPlanner(llm),
        retriever=HybridRetriever(),
        memory_store=PostgresMemoryStore(),
        memory_manager=LLMMemoryManager(llm),
        verifier=LexicalVerifier(),
        answer_generator=LLMAnswerGenerator(llm),
        llm=llm,
        top_k=top_k,
    )
