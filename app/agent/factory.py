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

__all__ = ["build_mock_orchestrator", "build_orchestrator"]


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
