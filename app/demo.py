"""端到端演示：从问题到带引用、带校验状态的答案。

运行：
    uv run python -m app.demo                      # 全 Mock，无需 key 与数据库
    uv run python -m app.demo --mode B1            # 换基线
    uv run python -m app.demo --real               # 真实模块（需 key + pgvector）
    uv run python -m app.demo --real -m B2 -q "..."  # 真实模块 + 指定模式

Mock 模式验证的是阶段 0 的验收项；--real 模式验证的是阶段 1 的集成：
    用户问题 → Router(LLM) → Planner(LLM) → 混合检索(B)
             → AnswerGenerator(LLM) → Verifier(C) → 答案 + 页码 + 校验状态
"""

from __future__ import annotations

import argparse
import sys

from app.agent.orchestrator import MODE_PRESETS
from app.core.types import AgentState, ClaimStatus

# --------------------------------------------------------------------------- #
# 终端着色（不支持时自动降级为纯文本）
# --------------------------------------------------------------------------- #
_COLOR = sys.stdout.isatty()


def _c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def dim(t: str) -> str:
    return _c(t, "2")


def bold(t: str) -> str:
    return _c(t, "1")


def green(t: str) -> str:
    return _c(t, "32")


def yellow(t: str) -> str:
    return _c(t, "33")


def red(t: str) -> str:
    return _c(t, "31")


def cyan(t: str) -> str:
    return _c(t, "36")


def hr(title: str = "") -> None:
    line = "─" * 66
    print(dim(line) if not title else f"{dim('──')} {bold(title)} {dim('─' * max(0, 60 - len(title)))}")


# --------------------------------------------------------------------------- #
# 渲染
# --------------------------------------------------------------------------- #
_STATUS_STYLE = {
    ClaimStatus.SUPPORTED: green,
    ClaimStatus.PARTIALLY_SUPPORTED: yellow,
    ClaimStatus.UNSUPPORTED: red,
    ClaimStatus.UNVERIFIED: yellow,
}


def render(state: AgentState) -> None:
    print()
    hr("问题")
    print(f"  {state.question}")

    hr("① Router")
    if state.route:
        r = state.route
        print(f"  路径      : {cyan(r.route.value)}")
        print(f"  需要检索  : {r.need_retrieval}    需要记忆: {r.need_memory}"
              f"    需要规划: {r.need_planning}")
        print(f"  理由      : {dim(r.reason)}")
    else:
        print(dim("  （未启用路由）"))

    if state.plan:
        hr("② Planner")
        for s in state.plan:
            print(f"  {s.step_id}. [{s.tool}] {s.description}  {dim('→ ' + (s.result_summary or ''))}")

    if state.memory_context:
        hr("③ 记忆读取")
        for m in state.memory_context:
            print(f"  · [{m.tier.value}] {m.content}  {dim(f'(importance={m.importance})')}")

    if state.evidence:
        hr("④ 检索证据")
        for i, ev in enumerate(state.evidence, start=1):
            print(f"  [{i}] {bold(ev.file_name)} 第 {bold(str(ev.page_number))} 页"
                  f"  {dim(f'score={ev.score} via={ev.retrieval_method.value}')}")
            print(f"      {dim(ev.text[:70])}...")

    hr("⑤ 回答")
    for line in state.final_answer.splitlines():
        print(f"  {line}")

    if state.citations:
        hr("⑥ 引用")
        for c in state.citations:
            print(f"  [{c.index}] {c.file_name} p.{c.page_number}")

    if state.claims:
        hr("⑦ Claim 校验")
        for c in state.claims:
            style = _STATUS_STYLE.get(c.status, dim)
            print(f"  {style('●')} {style(f'{c.status.value:<20}')} {c.claim[:50]}")
            if c.note:
                print(f"      {dim(c.note)}")
            if c.supporting_evidence:
                print(f"      {dim('证据: ' + ', '.join(c.supporting_evidence))}")

    hr("⑧ 用量与错误")
    u = state.usage
    print(f"  tokens={u.total_tokens}  calls={u.llm_calls}  "
          f"cost=${u.cost_usd:.6f}  latency={u.latency_ms}ms")
    if state.errors:
        for e in state.errors:
            print(f"  {red('✗')} {e}")
    else:
        print(f"  {green('✓')} 无错误")
    print()


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(description="端到端演示（默认全 Mock，--real 走真实模块）")
    parser.add_argument("--question", "-q", default="讲义里 Self-RAG 是怎么决定要不要检索的？")
    parser.add_argument(
        "--mode",
        "-m",
        default="B4",
        choices=sorted(MODE_PRESETS),
        help="B1=直接回答  B2=普通RAG  B3=RAG+记忆  B4=完整Agent",
    )
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument(
        "--real",
        action="store_true",
        help="用真实模块（LLM 路由/规划/生成 + 混合检索 + 词法校验）。"
        "需要 .env 配好 key、pgvector 已启动且已导入文档",
    )
    args = parser.parse_args()

    if args.real:
        from app.agent.factory import build_stage1_orchestrator
        from app.core.config import settings

        orch = build_stage1_orchestrator(top_k=args.top_k)
        api_key, base_url, model = settings.llm.resolve()
        print(bold("\n  真实链路  ·  模式 " + args.mode))
        if not api_key:
            print(
                f"  {yellow('!')} 未配置 API key，模型已退回 MockLLM；"
                "路由/规划会走关键词降级"
            )
        else:
            print(f"  模型：{model} @ {base_url}")
        print("  提示：若检索为空，先运行 uv run python scripts/index_pdfs.py")
    else:
        from app.agent.factory import build_mock_orchestrator

        orch = build_mock_orchestrator(top_k=args.top_k)
        print(bold(f"\n  Mock 端到端流程  ·  模式 {args.mode}"))

    flags = MODE_PRESETS[args.mode]
    print(f"  开关：{flags}")

    state = orch.run(args.question, session_id="demo", user_id="demo-user", **flags)
    render(state)

    # 简单的自检：让 CI / 手动验收都能一眼看出是否通过
    checks = [
        ("产生了最终答案", bool(state.final_answer.strip())),
        ("状态可通过 JSON 序列化", bool(state.model_dump_json())),
    ]
    if flags["enable_routing"]:
        checks.append(("Router 已决策", state.route is not None))
    if flags["enable_retrieval"]:
        checks.append(("检索返回了证据", len(state.evidence) > 0))
    else:
        # B1 必须真的不检索，否则 B1/B2 的实验就是同一个系统
        checks.append(("B1 未触发检索", len(state.evidence) == 0))
    if flags["enable_verification"]:
        checks.append(("Verifier 已产出 claim", len(state.claims) > 0))

    print(bold("  自检"))
    for name, ok in checks:
        print(f"  {green('✓') if ok else red('✗')} {name}")
    print()

    return 0 if all(ok for _, ok in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
