"""装配工厂。

阶段 0：全部用 Mock 装配，`build_mock_orchestrator()` 一行就能跑通。
阶段 1 之后：B / C 的模块完成后，把对应参数换成真实实现即可，
**Orchestrator 和 demo 代码都不用改**。
"""

from __future__ import annotations

from typing import Literal

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
    enable_replan: bool = True,
    max_replans: int = 1,
    web_search_tool: object | None = None,
    web_search_policy: object | None = None,
    enable_web_search: bool = True,
    web_query_rewriter: object | None = None,
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
        enable_replan=enable_replan,
        max_replans=max_replans,
        web_search_tool=web_search_tool,
        web_search_policy=web_search_policy,
        enable_web_search=enable_web_search,
        web_query_rewriter=web_query_rewriter,
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


def build_stage1_orchestrator(
    top_k: int = 5,
    memory_strategy: Literal["llm", "rule"] = "llm",
    enable_web_search: bool = True,
) -> Orchestrator:
    """阶段 1 的真实装配：能跑真模型的链路。

    各模块运行时实际走哪个文件：

        Router            app/routing/router.py           LLMRouter
        Planner           app/planning/planner.py         LLMPlanner
        Retriever         app/retrieval/                   HybridRetriever（B）
        MemoryStore       app/memory/postgres_store.py    PostgresMemoryStore（B）
        MemoryManager     app/memory/manager.py           LLMMemoryManager（A，默认）
        Verifier          app/verification/                SemanticVerifier（C）
        AnswerGenerator   app/agent/answer_generator.py   LLMAnswerGenerator
        LLM               app/llm/client.py               get_llm()

    `memory_strategy` 只影响记忆策略这一处：
      - "llm"（默认）：`app/memory/manager.py` 的 `LLMMemoryManager`，让模型判断
      - "rule"：`app/memory/policy.py` 的 `RuleBasedMemoryManager`，纯规则零成本
    规则版是 B 早期垫的实现，保留作 RQ2「规则 vs 模型」的消融基线。

    前置条件：
      - `.env` 里配好 API key，否则 `get_llm()` 会退回 `MockLLM`，
        路由与规划会自动走关键词降级，链路仍能跑但质量等同 Mock
      - pgvector 已启动，且已用 `scripts/index_pdfs.py` 导入文档，
        否则检索返回空、回答会明确告知"资料不足"

    这里用函数内延迟导入：`build_mock_orchestrator()` 是阶段 0 的入口，
    不应该因为 psycopg / httpx 之类的依赖而变得不可用。
    """
    from app.agent.answer_generator import LLMAnswerGenerator
    from app.agent.web_query_rewriter import WebQueryRewriter
    from app.core.config import settings
    from app.llm.client import get_llm
    from app.memory import LLMMemoryManager, PostgresMemoryStore, RuleBasedMemoryManager
    from app.planning.planner import LLMPlanner
    from app.retrieval import HybridRetriever
    from app.retrieval.web_search import WebSearchTool
    from app.routing.router import LLMRouter
    from app.verification import LexicalVerifier, SemanticVerifier

    llm = get_llm()

    def build_web_tool():
        """Issue #89/#96：有 Tavily key 走 API，失败/空结果自动回退 HTML 爬取。"""
        if not enable_web_search:
            return None
        if settings.search.tavily_api_key:
            from app.agent.web_search_fallback import FallbackSearchTool
            from app.retrieval.tavily_search import TavilySearchTool
            # B review：key 配了但 API 失败/空结果时不能直接返回空，要降级到 HTML
            return FallbackSearchTool(TavilySearchTool(), WebSearchTool())
        return WebSearchTool()

    if memory_strategy == "rule":
        memory_manager = RuleBasedMemoryManager()
    elif memory_strategy == "llm":
        memory_manager = LLMMemoryManager(llm)
    else:
        raise ValueError(f"未知的 memory_strategy: {memory_strategy!r}，只支持 'llm' / 'rule'")

    return build_orchestrator(
        router=LLMRouter(llm),
        planner=LLMPlanner(llm),
        retriever=HybridRetriever(),
        memory_store=PostgresMemoryStore(),
        memory_manager=memory_manager,
        verifier=SemanticVerifier(llm, fallback=LexicalVerifier()),
        answer_generator=LLMAnswerGenerator(llm),
        llm=llm,
        top_k=top_k,
        web_search_tool=build_web_tool(),
        enable_web_search=enable_web_search,
        # Issue #89：真实链路用 LLM 把口语问句改写成搜索关键词
        web_query_rewriter=WebQueryRewriter(llm) if enable_web_search else None,
    )
