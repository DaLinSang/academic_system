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
    StepStatus,
    ToolResult,
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
        enable_replan: bool = True,
        max_replans: int = 1,
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
        # Issue #47：校验失败后的重规划预算。默认 1 次——再多成本失控，
        # 而且 RQ4 会把"修复过程"和"正常回答"混在一起统计。
        self.enable_replan = enable_replan
        self.max_replans = max(0, min(max_replans, 2))

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
        replan_budget = self.max_replans  # 每次 run 独立预算，绝不改实例配置
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
                    self._absorb_usage(state, self.planner)
                    self._record_retry(state, "planner")
                    state.log("plan", "end", steps=len(state.plan))

        # 4) 工具执行（当前只有检索；web_search 与代码执行留给后续迭代）
        if route.need_retrieval and state.enable_retrieval:
            self._run_retrieval(state)

        # 5) 生成草稿
        with self._stage(state, "generate") as ok:
            if ok:
                state.draft_answer = self.answer_generator.generate(state)
                state.build_citations()
                self._record_retry(state, "generate")
                state.log("generate", "end", chars=len(state.draft_answer))

        # 6) 校验
        if enable_verification:
            self._run_verification(state)

            # 6.5) 校验发现问题后的重规划（Issue #47）。
            # 预算是"每次 run"的局部变量，不是实例字段——Streamlit 会缓存并复用
            # 同一个 Orchestrator，改实例字段会让第一个请求之后永久失去重规划能力。
            if (
                self.enable_replan
                and state.unsupported_claims
                and route.need_planning
            ):
                if replan_budget > 0:
                    replan_budget = self._maybe_replan(state, route, replan_budget)
                else:
                    state.log("replan", "skip", reason="budget_exhausted")

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
                self._record_retry(state, "router")
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
        """执行检索工具，并把每次调用记进 `tool_results`。

        计划里带 query 的 retrieval 步骤逐条执行；没有计划时退回用原问题检索。

        每个步骤的执行结果回填到 `PlanStep.status` / `result_summary`，
        这样「哪一步成功、哪一步失败、各命中几条」在 trace 和 UI 里都看得到。
        在此之前 `tool_results` 恒为空、步骤状态永远停在 PENDING，
        失败案例分析拿不到任何工具级数据。
        """
        planned = [s for s in state.plan if s.tool == "retrieval" and s.query]
        jobs: list = [(s, s.query) for s in planned] if planned else [(None, state.question)]

        with self._stage(state, "retrieval") as ok:
            if not ok:
                return

            # C review #38：HybridRetriever.retrieve() 每次调用开头都会把
            # last_warning 重置为 None。必须在每次调用后立刻读取，否则多查询
            # 时最后一次成功会把前面所有降级记录抹掉。
            degradations: list[dict[str, object]] = []

            for step, query in jobs:
                started = time.perf_counter()
                step_id = step.step_id if step is not None else None
                try:
                    found = self.retriever.retrieve(query, top_k=self.top_k)
                except Exception as exc:  # noqa: BLE001 - 跨模块边界统一兜底
                    latency = round((time.perf_counter() - started) * 1000, 2)
                    message = f"{type(exc).__name__}: {exc}"
                    # record_tool 失败时会同时写入 state.errors
                    state.record_tool(
                        ToolResult(
                            tool_name="retrieval",
                            success=False,
                            error=message,
                            latency_ms=latency,
                            metadata={"query": query, "step_id": step_id},
                        )
                    )
                    state.log("retrieval", "error", error=message, step_id=step_id)
                    if step is not None:
                        step.status = StepStatus.FAILED
                        step.result_summary = message[:120]
                    continue

                latency = round((time.perf_counter() - started) * 1000, 2)
                state.add_evidence(found)

                # 立即读取本次调用的降级原因，不让它被下一次 retrieve 清掉
                warning = getattr(self.retriever, "last_warning", None)
                if warning:
                    degradations.append(
                        {"query": query, "step_id": step_id, "reason": warning}
                    )
                    state.log(
                        "retrieval",
                        "degraded",
                        reason=warning,
                        query=query,
                        step_id=step_id,
                    )

                state.record_tool(
                    ToolResult(
                        tool_name="retrieval",
                        success=True,
                        output={"count": len(found)},
                        latency_ms=latency,
                        metadata={"query": query, "step_id": step_id},
                    )
                )
                if step is not None:
                    step.status = StepStatus.DONE
                    step.result_summary = f"命中 {len(found)} 个片段"

            # llm 类步骤当前不单独执行，分析统一在 AnswerGenerator 里完成。
            # 标 SKIPPED 而不是 PENDING —— PENDING 暗示"还没走到"，但它其实
            # 已经被有意跳过了，如实记录才不会让报告误读。
            for step in state.plan:
                if step.tool == "llm" and step.status is StepStatus.PENDING:
                    step.status = StepStatus.SKIPPED
                    step.result_summary = "由 AnswerGenerator 统一完成，不作为独立工具执行"

            # 已处理（DONE / FAILED / SKIPPED）的步骤数。
            state.current_step = sum(
                1 for s in state.plan if s.status is not StepStatus.PENDING
            )
            # 汇总所有查询的降级原因。多查询场景下只报最后一个会误导 RQ1/RQ3。
            merged_warning = "；".join(str(d["reason"]) for d in degradations) or None
            state.log(
                "retrieval",
                "end",
                evidence=len(state.evidence),
                queries=len(jobs),
                tools_recorded=len(state.tool_results),
                warning=merged_warning,
                degraded=bool(degradations),
                degraded_queries=[str(d["query"]) for d in degradations],
            )

    def _run_verification(self, state: AgentState) -> None:
        with self._stage(state, "verify") as ok:
            if not ok:
                # 校验器崩溃时不能让整个请求失败：降级为"未校验"
                state.log("verify", "skip", reason="verifier unavailable")
                return
            if not state.evidence:
                state.claims = []
                state.log(
                    "verify",
                    "skip",
                    reason="no_document_evidence",
                    route=state.route.route if state.route else None,
                )
                return
            try:
                state.claims = self.verifier.verify(state.draft_answer, state.evidence)
                self._absorb_usage(state, self.verifier)
                self._record_retry(state, "verify")
                state.log(
                    "verify",
                    "end",
                    claims=len(state.claims),
                    unsupported=len(state.unsupported_claims),
                )
            except Exception as exc:  # noqa: BLE001
                state.errors.append(f"verifier: {type(exc).__name__}: {exc}")
                state.log("verify", "error", error=str(exc))

    def _maybe_replan(
        self, state: AgentState, route: RouteDecision, budget: int
    ) -> int:
        """校验发现可判定的 unsupported claim 后再规划一轮，返回剩余预算。

        不给 `Planner.plan()` 加参数（那是公共契约）：把失败信息拼进 question
        字符串，Mock 与真实 Planner 都能直接吃。

        C review #52：SemanticVerifier 在无法判断时会输出
        `status=UNSUPPORTED + note="UNVERIFIED: ..."`，这不是"事实被否定"，
        不能因此触发额外规划/检索/生成，否则 RQ3/RQ4 会被污染。
        """
        if budget < 1:
            state.log("replan", "skip", reason="budget_exhausted")
            return budget

        # 只把"能判定为无支撑"的 claim 作为重规划输入
        determinable = [
            c for c in state.unsupported_claims
            if not (c.note or "").startswith("UNVERIFIED:")
        ]
        if not determinable:
            state.log(
                "replan",
                "skip",
                reason="unverified_only" if state.unsupported_claims else "no_unsupported",
            )
            return budget

        unsupported = [c.claim for c in determinable][:3]
        retry_question = (
            f"{state.question}\n\n"
            f"上一轮回答中以下内容缺少证据，请规划补充检索：{'；'.join(unsupported)}"
        )

        with self._stage(state, "replan") as ok:
            if not ok:
                return budget
            try:
                new_plan = self.planner.plan(retry_question, route, state.memory_context)
            except Exception as exc:  # noqa: BLE001 - 重规划失败不能毁掉已有回答
                state.errors.append(f"replan: {type(exc).__name__}: {exc}")
                state.log("replan", "error", error=str(exc))
                return budget

            self._absorb_usage(state, self.planner)
            state.log(
                "replan",
                "start",
                unsupported=len(determinable),
                steps=len(new_plan),
            )

            # 用新的检索查询补证据；state.add_evidence 按 chunk_id 去重，不会重复
            self._run_retrieval_with_plan(state, new_plan)

            # 重新生成与校验一次，然后无条件停手
            with self._stage(state, "generate") as gen_ok:
                if gen_ok:
                    state.draft_answer = self.answer_generator.generate(state)
                    state.build_citations()
                    state.log("generate", "end", chars=len(state.draft_answer), replan=True)
            if state.evidence:
                self._run_verification(state)

            budget -= 1
            state.log(
                "replan",
                "end",
                evidence=len(state.evidence),
                remaining_unsupported=len([c for c in state.unsupported_claims if not (c.note or "").startswith("UNVERIFIED:")]),
            )
            return budget

    def _run_retrieval_with_plan(self, state: AgentState, plan: list) -> None:
        """用给定计划执行检索；复用主路径的逻辑但临时替换 plan。"""
        original = state.plan
        state.plan = plan
        try:
            self._run_retrieval(state)
        finally:
            # 保留重规划的步骤供报告查看，但把状态恢复为"本轮最终计划"
            state.plan = original + [s for s in plan if s not in original]

    def _write_memory(self, state: AgentState) -> None:
        """只在 enable_memory 时写入，且只写 MemoryManager 认为值得存的。"""
        with self._stage(state, "memory_write") as ok:
            if not ok:
                return
            try:
                candidates = self.memory_manager.extract_candidates(
                    state.question, state.final_answer, state.memory_context
                )
                self._absorb_usage(state, self.memory_manager)
                self._record_retry(state, "memory_write")
                for c in candidates:
                    self.memory_store.add(c, user_id=state.user_id)

                # 记忆漏斗：模型提议几条、被敏感/重复/非法各拦掉几条。
                # RQ2 分析"记忆写入拒绝率"直接用这个，比只报最终条数有说服力。
                stats = getattr(self.memory_manager, "last_stats", None)
                if isinstance(stats, dict) and stats:
                    state.log("memory_write", "stats", **stats)

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

    def _record_retry(self, state: AgentState, stage: str) -> None:
        """记录刚发生的 LLM 重试，标明属于哪个阶段。

        C review #53：LLMRouter / LLMPlanner / SemanticVerifier / LLMMemoryManager
        这些包装组件没有 `last_retries`，计数器在它们共享的 `self.llm` 上。
        此前从 component 读取，真实链路里永远拿不到，测试还是假阳性。
        """
        retries = getattr(self.llm, "last_retries", 0)
        if retries:
            state.log(
                "llm",
                "retry",
                phase=stage,
                retries=retries,
                retry_latency_ms=round(
                    float(getattr(self.llm, "last_retry_latency_ms", 0.0)), 2
                ),
            )
            # 消费掉计数器：否则同一个 LLM 实例会让后续所有阶段都误记一次重试
            self.llm.last_retries = 0
            self.llm.last_retry_latency_ms = 0.0

    # ------------------------------------------------------------------ #
    # 最终答案组装
    # ------------------------------------------------------------------ #
    def _compose_final(self, state: AgentState) -> str:
        body = state.draft_answer or "抱歉，本次没有生成有效回答。"

        # 没有检索到任何证据时，答案本身已经是"无证据"声明，
        # 再列一遍 unsupported 只会重复报警，反而降低可读性。
        if not state.enable_verification or not state.claims or not state.evidence:
            return body

        unverified = [
            c
            for c in state.claims
            if c.status == ClaimStatus.UNSUPPORTED
            and (c.note or "").startswith("UNVERIFIED:")
        ]
        unsupported = [
            c
            for c in state.claims
            if c.status == ClaimStatus.UNSUPPORTED and c not in unverified
        ]
        partial = [c for c in state.claims if c.status == ClaimStatus.PARTIALLY_SUPPORTED]

        notes: list[str] = []
        if unverified:
            notes.append("ℹ️ 以下内容现有证据不足，校验器未能可靠确认：")
            notes.extend(f"  - {c.claim}" for c in unverified)
        if unsupported:
            notes.append("⚠️ 以下内容与已检索证据不一致或缺少支持，请谨慎采信：")
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
