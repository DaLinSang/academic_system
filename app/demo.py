"""阶段 0 的端到端演示：用 Mock 跑通整条链路。

运行：
    uv run python -m app.demo
    uv run python -m app.demo --mode B4 --question "比较 RAG 和 ReAct 的区别"
    uv run python -m app.demo --mode B1

它验证的是分工文档里阶段 0 的验收项：
    用户问题 → Mock Router → Mock Retriever → Mock LLM Response
             → Mock Verifier → 返回答案、引用和验证状态
"""

from __future__ import annotations

import argparse
import sys

from app.agent.factory import build_mock_orchestrator
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
    parser = argparse.ArgumentParser(description="阶段 0 Mock 端到端演示")
    parser.add_argument("--question", "-q", default="讲义里 Self-RAG 是怎么决定要不要检索的？")
    parser.add_argument(
        "--mode",
        "-m",
        default="B4",
        choices=sorted(MODE_PRESETS),
        help="B1=直接回答  B2=普通RAG  B3=RAG+记忆  B4=完整Agent",
    )
    parser.add_argument("--top-k", type=int, default=3)
    args = parser.parse_args()

    orch = build_mock_orchestrator(top_k=args.top_k)
    flags = MODE_PRESETS[args.mode]

    print(bold(f"\n  Mock 端到端流程  ·  模式 {args.mode}  ·  {flags}"))
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
