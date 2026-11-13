"""Agent Orchestrator（A 负责）。

整条链路：
    用户问题
      → 读取记忆（可选）
      → Router 决定路径
      → Planner 拆解（仅 complex_task）
      → 执行检索 / 其他工具
      → 生成带引用的草稿
      → Verifier 逐条校验
      → 返回最终答案
      → 写入必要记忆

设计要点
--------
1. **所有状态通过 AgentState 传递**，不用全局变量（分工文档硬性要求）。
2. **每个阶段都包在 _stage() 里**：抛异常不会炸掉整条链路，而是记录 error 并降级。
   这直接对应 Proposal 的 "recovering from tool errors" 和 RQ4 的 recovery success 指标。
3. **B1–B4 只靠开关区分**，不复制四份代码 —— 否则评测结果不可比。
"""

from __future__ import annotations

import time
import traceback
from collections.abc import Iterator
from contextlib import contextmanager

from app.core.errors import AcademicSystemError
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
from app.core.types import (
    AgentState,
    ClaimStatus,
    RouteDecision,
    RouteType,
    Usage,
)

__all__ = ["Orchestrator", "MODE_PRESETS"]


# B1–B4 的消融预设。C 的评测脚本直接按名字取，保证四个版本输入完全一致。
# 关键：B1 与 B2 的差别**只在 enable_retrieval**，不要写成同一套配置，
# 否则两组实验实际跑的是同一个系统，结论直接作废。
MODE_PRESETS: dict[str, dict[str, bool]] = {
    "B1": {
        "enable_routing": False,
        "enable_retrieval": False,
        "enable_memory": False,
        "enable_verification": False,
    },
    "B2": {
        "enable_routing": False,
        "enable_retrieval": True,
        "enable_memory": False,
        "enable_verification": False,
    },
    "B3": {
        "enable_routing": True,
        "enable_retrieval": True,
        "enable_memory": True,
        "enable_verification": False,
    },
    "B4": {
        "enable_routing": True,
        "enable_retrieval": True,
        "enable_memory": True,
        "enable_verification": True,
    },
}


class Orchestrator:
    """把所有模块拼成一条可运行的 Agent 流水线。

    构造时注入实现，因此 Mock 与真实模块可以任意互换 —— 这正是阶段 0 的目的。
    """

    def __init__(
        self,
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
    ) -> None:
        self.router = router
        self.planner = planner
        self.retriever = retriever
        self.memory_store = memory_store
        self.memory_manager = memory_manager
        self.verifier = verifier
        self.answer_generator = answer_generator
        self.llm = llm
        self.top_k = top_k

    # ------------------------------------------------------------------ #
    # 主入口
    # ------------------------------------------------------------------ #
    def run(
        self,
        question: str,
        *,
        session_id: str = "default",
        user_id: str = "default",
        turn_index: int = 0,
        enable_routing: bool = True,
        enable_retrieval: bool = True,
        enable_memory: bool = True,
        enable_verification: bool = True,
    ) -> AgentState:
        state = AgentState(
            question=question,
            session_id=session_id,
            user_id=user_id,
            turn_index=turn_index,
            enable_routing=enable_routing,
            enable_retrieval=enable_retrieval,
            enable_memory=enable_memory,
            enable_verification=enable_verification,
        )
        t_total = time.perf_counter()
        state.log("pipeline", "start", question=question)

        # 1) 记忆读取
        if enable_memory:
            with self._stage(state, "memory_read") as ok:
                if ok:
                    state.memory_context = self.memory_store.search(
                        question, user_id=user_id, top_k=self.top_k
                    )
                    state.log("memory_read", "end", hits=len(state.memory_context))

        # 2) 路由
        route = self._decide_route(state, enable_routing)

        # 3) 规划
        if route.need_planning:
            with self._stage(state, "plan") as ok:
                if ok:
                    state.plan = self.planner.plan(question, route, state.memory_context)
                    state.log("plan", "end", steps=len(state.plan))

        # 4) 工具执行（当前只有检索；web_search 与代码执行留给后续迭代）
        if route.need_retrieval and state.enable_retrieval:
            self._run_retrieval(state)

        # 5) 生成草稿
        with self._stage(state, "generate") as ok:
            if ok:
                state.draft_answer = self.answer_generator.generate(state)
                state.build_citations()
                state.log("generate", "end", chars=len(state.draft_answer))

        # 6) 校验
        if enable_verification:
            self._run_verification(state)

        # 7) 组装最终答案
        state.final_answer = self._compose_final(state)

        # 8) 写回记忆
        if enable_memory:
            self._write_memory(state)

        state.usage = state.usage.add(
            Usage(latency_ms=round((time.perf_counter() - t_total) * 1000, 2))
        )
        state.log(
            "pipeline",
            "end",
            total_latency_ms=state.usage.latency_ms,
            tokens=state.usage.total_tokens,
            errors=len(state.errors),
        )
        return state

    # ------------------------------------------------------------------ #
    # 各阶段
    # ------------------------------------------------------------------ #
    def _decide_route(self, state: AgentState, enable_routing: bool) -> RouteDecision:
        """路由；关闭路由时按 enable_retrieval 退化为 B1 或 B2。"""
        if not enable_routing:
            if state.enable_retrieval:
                # B2：普通 RAG —— 不做决策，一律检索
                route = RouteDecision(
                    route=RouteType.DOCUMENT_RETRIEVAL,
                    need_retrieval=True,
                    need_memory=False,
                    reason="routing disabled: fallback to plain RAG (B2)",
                    confidence=1.0,
                )
            else:
                # B1：直接 LLM —— 不检索、不看记忆
                route = RouteDecision(
                    route=RouteType.DIRECT,
                    need_retrieval=False,
                    need_memory=False,
                    reason="routing disabled, retrieval disabled: direct LLM (B1)",
                    confidence=1.0,
                )
            state.route = route
            state.log("router", "skip", reason=route.reason)
            return route

        with self._stage(state, "router") as ok:
            if ok:
                route = self.router.route(
                    state.question,
                    has_documents=True,
                    memory_context=state.memory_context,
                )
                self._absorb_usage(state, self.router)
                state.route = route
                state.log("router", "end", route=str(route.route), reason=route.reason)
                return route

        # 路由失败 → 保守地走检索，避免"没检索导致答不出来"
        fallback = RouteDecision(
            route=RouteType.DOCUMENT_RETRIEVAL,
            need_retrieval=True,
            reason="router failed, fallback to retrieval",
        )
        state.route = fallback
        return fallback

    def _run_retrieval(self, state: AgentState) -> None:
        queries = [s.query for s in state.plan if s.query] or [state.question]
        with self._stage(state, "retrieval") as ok:
            if not ok:
                return
            for q in queries:
                try:
                    found = self.retriever.retrieve(q, top_k=self.top_k)
                    state.add_evidence(found)
                except AcademicSystemError as exc:
                    state.errors.append(f"retrieval: {exc}")
                    state.log("retrieval", "error", error=str(exc))
                except Exception as exc:  # noqa: BLE001 - 跨模块边界统一兜底
                    state.errors.append(f"retrieval: unexpected {type(exc).__name__}: {exc}")
                    state.log("retrieval", "error", error=str(exc))
            state.log("retrieval", "end", evidence=len(state.evidence), queries=len(queries))

    def _run_verification(self, state: AgentState) -> None:
        with self._stage(state, "verify") as ok:
            if not ok:
                # 校验器崩溃时不能让整个请求失败：降级为"未校验"
                state.log("verify", "skip", reason="verifier unavailable")
                return
            try:
                state.claims = self.verifier.verify(state.draft_answer, state.evidence)
                state.log(
                    "verify",
                    "end",
                    claims=len(state.claims),
                    unsupported=len(state.unsupported_claims),
                )
            except Exception as exc:  # noqa: BLE001
                state.errors.append(f"verifier: {type(exc).__name__}: {exc}")
                state.log("verify", "error", error=str(exc))

    def _write_memory(self, state: AgentState) -> None:
        """只在 enable_memory 时写入，且只写 MemoryManager 认为值得存的。"""
        with self._stage(state, "memory_write") as ok:
            if not ok:
                return
            try:
                candidates = self.memory_manager.extract_candidates(
                    state.question, state.final_answer, state.memory_context
                )
                for c in candidates:
                    self.memory_store.add(c, user_id=state.user_id)
                state.log("memory_write", "end", stored=len(candidates))
            except Exception as exc:  # noqa: BLE001
                state.errors.append(f"memory_write: {type(exc).__name__}: {exc}")
                state.log("memory_write", "error", error=str(exc))

    # ------------------------------------------------------------------ #
    # 用量归集
    # ------------------------------------------------------------------ #
    def _absorb_usage(self, state: AgentState, component: object) -> None:
        """把组件内部产生的 token / 成本累加进 state。

        背景：`Router.route()` 的签名（契约已冻结）不接收 `AgentState`，
        但它在内部确实调用了一次 LLM。若不在这里回收，RQ4 的成本与延迟
        统计会系统性偏低，B1–B4 的对比就失真了。

        约定：组件若自己调过模型，就把本次用量放在 `last_usage` 属性上。
        Mock 没有这个属性，`getattr` 返回 None，静默跳过。
        """
        usage = getattr(component, "last_usage", None)
        if isinstance(usage, Usage):
            state.usage = state.usage.add(usage)

    # ------------------------------------------------------------------ #
    # 最终答案组装
    # ------------------------------------------------------------------ #
    def _compose_final(self, state: AgentState) -> str:
        body = state.draft_answer or "抱歉，本次没有生成有效回答。"

        # 没有检索到任何证据时，答案本身已经是"无证据"声明，
        # 再列一遍 unsupported 只会重复报警，反而降低可读性。
        if not state.enable_verification or not state.claims or not state.evidence:
            return body

        unsupported = [c for c in state.claims if c.status == ClaimStatus.UNSUPPORTED]
        partial = [c for c in state.claims if c.status == ClaimStatus.PARTIALLY_SUPPORTED]

        notes: list[str] = []
        if unsupported:
            notes.append("⚠️ 以下内容在已检索到的证据中没有找到支持，请谨慎采信：")
            notes.extend(f"  - {c.claim}" for c in unsupported)
        if partial:
            notes.append("ℹ️ 以下内容仅有部分证据支持：")
            notes.extend(f"  - {c.claim}" for c in partial)

        return body if not notes else body + "\n\n" + "\n".join(notes)

    # ------------------------------------------------------------------ #
    # 错误兜底
    # ------------------------------------------------------------------ #
    @contextmanager
    def _stage(self, state: AgentState, name: str) -> Iterator[bool]:
        """包裹单个阶段：记录耗时，异常时记录并返回 False 让调用方降级。"""
        t0 = time.perf_counter()
        try:
            yield True
        except Exception as exc:  # noqa: BLE001
            latency = round((time.perf_counter() - t0) * 1000, 2)
            msg = f"{name}: {type(exc).__name__}: {exc}"
            state.errors.append(msg)
            state.log(
                name,
                "error",
                latency,
                error=msg,
                traceback=traceback.format_exc(limit=3),
            )
            yield False
        else:
            state.log(name, "timing", round((time.perf_counter() - t0) * 1000, 2))
