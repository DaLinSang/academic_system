"""LLM 驱动的请求路由器（A 负责）。

Router 回答一个问题：**这次请求该走哪条路？**

三分类（与 `RouteType` 一致）：
  - direct              → 常识性问题，直接用模型自身知识回答，不检索
  - document_retrieval  → 答案在用户自己的 PDF / 讲义 / 笔记里，需要检索
  - complex_task        → 需要多步完成（比较多个文档、生成计划或报告），交给 Planner

为什么这是全项目最关键的一个模块
--------------------------------
Proposal 的 RQ1 就是「agent 能否选对信息来源」。如果 Router 退化成一堆 if-else，
这个项目就只是"套了壳的 RAG"，会被判 *standard RAG pipeline without agentic
decision making*。所以这里的实现必须：① 真的用模型做语义判断；② 决策理由可记录、
可评测；③ 失败时有确定的降级路径，而不是崩溃。

设计要点
--------
1. **temperature=0**：路由必须是确定性的，否则同一问题两次运行结论不同，
   RQ1 的 tool-selection accuracy 无法测量。
2. **JSON 输出 + 严格归一化**：模型返回什么都不能直接信，统一走
   `_normalise()` 修正逻辑矛盾（例如 route=direct 却 need_retrieval=true）。
3. **两级降级**：JSON 解析失败 → 关键词启发式；LLM 调用失败 → 关键词启发式。
   任何情况下都返回一个合法的 `RouteDecision`，绝不抛异常给 Orchestrator。
4. **usage 要能带走**：Router 内部调用 LLM 会产生 token 成本，但 `route()` 的签名
   不接收 `AgentState`（契约已冻结，不改）。因此把用量挂在 `last_usage` 上，
   由 Orchestrator 读取后累加，保证 RQ4 的成本统计不漏。
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.agent.history_context import format_history
from app.core.interfaces import LLM
from app.core.types import ConversationTurn, MemoryItem, RouteDecision, RouteType, Usage

__all__ = ["LLMRouter", "RuleBasedRouter"]

# --------------------------------------------------------------------------- #
# 提示词
# --------------------------------------------------------------------------- #
_SYSTEM_PROMPT = """你是学术知识助手的路由器。判断用户请求属于哪一类，并输出 json。

三类定义：
- direct：常识性、通用知识、闲聊、翻译或润色。答案不依赖用户自己的文档。
- document_retrieval：答案在用户自己的 PDF、讲义、笔记、论文里。需要检索。
- complex_task：需要多步完成。例如比较多个文档、生成研究计划、撰写短报告、梳理综述。

判断规则：
1. 出现"比较/对比/综述/研究计划/写一份/报告/梳理"等要求产出多段结论的，归为 complex_task。
   英文同理：compare / contrast / literature review / research plan / summarize several documents。
2. 指向具体材料（"这篇论文""讲义里""第几页""我的笔记"，或英文 this paper / the lecture /
   according to / in the PDF / uploaded document）的，归为 document_retrieval。
3. 纯概念解释且用户没有提供文档的（"什么是 attention"），归为 direct。
4. need_retrieval：是否需要检索用户文档。
5. need_planning：是否只在 complex_task 时为 true。
6. reason 用一句中文说明依据，会写进实验日志，必须具体，不要写"根据规则"。
7. confidence 是 0 到 1 的小数。
8. need_memory 表示"本轮读到的记忆是否对回答有帮助"。注意：是否**读取**记忆由
   Orchestrator 的 enable_memory 开关决定（B3/B4 开、B1/B2 关），与本字段无关；
   本字段只是路由对已有记忆有用性的判断。没有记忆时填 false。
9. 英文用户有文档时，不要把所有问句都当成通用知识。像 "What problem does X solve?"、
   "What are the limitations of X?" 这类针对某个具体方法的评价性问题，用户通常期待基于
   材料回答，应归 document_retrieval。只有闲聊、翻译、润色、天气等明显与文档无关的请求
   才归 direct。
10. need_web_search：是否需要联网查外部网页。用户明确要求"查询官网/搜索网页/找最新公开
   信息/给出网页链接"，或问题依赖本地文档不可能包含的外部实时事实时填 true，即使本地有
   文档证据。例："查香港大学官网 COMP7608 最新介绍并给链接" → true。
   普通文档问答（"讲义里 Self-RAG 是什么"）填 false。

只输出下面这个 json 对象，不要输出任何其他内容：
{"route": "direct|document_retrieval|complex_task", "need_retrieval": true, "need_memory": false, "need_planning": false, "need_web_search": false, "confidence": 0.0, "reason": ""}"""

# --------------------------------------------------------------------------- #
# 关键词启发式：只作为降级路径，不参与正常路由
# --------------------------------------------------------------------------- #
_COMPLEX_KEYWORDS = (
    # 中文
    "比较", "对比", "综述", "研究计划", "写一份", "写个", "报告", "梳理", "多个", "几篇", "总结一下",
    # 英文：B 的 Demo PDF 全是英文，无 key 时降级路径必须能识别这些意图
    "compare", "comparison", "contrast", "summarize", "summary of",
    "literature review", "research plan", "write a report", "write an essay",
    "overview of", "survey of", "multiple papers", "several documents",
)
# 只保留**强文档指代**。"什么是""定义""提到"这类词太泛——
# "什么是 attention" 是典型的不需要检索的问题，若把它算作检索，
# RQ1 的"不必要检索率"会被系统性抬高，指标失去意义。
_RETRIEVAL_KEYWORDS = (
    # 中文
    "这篇", "文档", "论文", "讲义", "笔记", "第几页", "哪一页", "课件", "我上传", "附件",
    # 英文（小写匹配，见 _rule_based_route）
    "this paper", "the paper", "the article", "the document", "the lecture",
    "lecture notes", "these notes", "my notes", "the slides", "the pdf",
    "in the pdf", "according to", "based on the document", "from the paper",
    "uploaded", "attachment", "attached paper", "attached document",
    "which page", "what page", "page 9",
    # Issue #25 的原句："What problem does Retrieval-Augmented Generation solve?"
    # 没有显式文档指代，但在有文档的语境下是典型的"问材料内容"。
    "what problem does", "what problems does", "what are the limitations",
    "retrieval-augmented", "retrieval augmented",
)

# 模型可能给出的别名，统一映射到 RouteType
_ROUTE_ALIASES: dict[str, RouteType] = {
    "direct": RouteType.DIRECT,
    "direct_answer": RouteType.DIRECT,
    "no_retrieval": RouteType.DIRECT,
    "document_retrieval": RouteType.DOCUMENT_RETRIEVAL,
    "retrieval": RouteType.DOCUMENT_RETRIEVAL,
    "rag": RouteType.DOCUMENT_RETRIEVAL,
    "document": RouteType.DOCUMENT_RETRIEVAL,
    "complex_task": RouteType.COMPLEX_TASK,
    "complex": RouteType.COMPLEX_TASK,
    "planning": RouteType.COMPLEX_TASK,
    "multi_step": RouteType.COMPLEX_TASK,
}

_FENCE = re.compile(r"```(?:json)?\s*(.+?)```", re.DOTALL)


# --------------------------------------------------------------------------- #
# 解析工具
# --------------------------------------------------------------------------- #
def _extract_json_object(text: str) -> dict[str, Any]:
    """从模型输出里抠出第一个完整的 JSON 对象。

    真实模型经常不听话：前面加一句"好的，分析如下"，外面套 ```json 围栏，
    或者在 JSON 后面再补一段解释。这三种情况都要能吃下去。
    """
    raw = text.strip()

    fenced = _FENCE.search(raw)
    if fenced:
        raw = fenced.group(1).strip()

    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    # 回退：手工扫描第一个括号平衡的片段
    start = raw.find("{")
    if start == -1:
        raise ValueError("模型输出里没有 JSON 对象")

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(raw)):
        char = raw[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                parsed = json.loads(raw[start : index + 1])
                if isinstance(parsed, dict):
                    return parsed
                raise ValueError("JSON 顶层不是对象")

    raise ValueError("JSON 括号不配对")


def _as_bool(value: Any, default: bool = False) -> bool:
    """模型可能返回 true / "true" / 1 / "yes"，统一成 bool。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1", "是", "需要"}
    return default


def _as_confidence(value: Any, default: float = 0.5) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, score))


def _as_route(value: Any) -> RouteType:
    key = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    if key in _ROUTE_ALIASES:
        return _ROUTE_ALIASES[key]
    raise ValueError(f"未知的路由类别: {value!r}")


def _normalise(
    route: RouteType,
    need_retrieval: bool,
    need_memory: bool,
    need_planning: bool,
    has_documents: bool,
) -> tuple[RouteType, bool, bool, bool]:
    """消除模型输出里的逻辑矛盾。

    这一步是 RQ1 评测可信的前提：如果 direct 的样本里出现 need_retrieval=True，
    "不必要的检索率"这个指标就没法定量。所以这里强制三类各自的约束。
    """
    # 没有可用文档时，不可能走文档检索
    if route == RouteType.DOCUMENT_RETRIEVAL and not has_documents:
        route = RouteType.DIRECT

    if route == RouteType.DIRECT:
        return route, False, need_memory, False
    if route == RouteType.DOCUMENT_RETRIEVAL:
        return route, True, need_memory, False
    # complex_task：必须有规划；检索取决于是否有文档
    return route, need_retrieval and has_documents, need_memory, True


def _heuristic_route(question: str, has_documents: bool) -> RouteDecision:
    """降级路径：关键词判断。

    只在模型不可用或输出无法解析时使用。故意写得保守——宁可多检索一次，
    也不要漏掉一个本该查文档的问题（后者对用户体验伤害更大）。
    """
    text = question.strip()
    # casefold 对中文无影响，只让英文关键词大小写不敏感。
    # Issue #25：B 的 Demo PDF 全是英文，降级路径此前只认中文，导致英文问题
    # 被误判成 direct、检索根本不触发。
    lowered = text.casefold()

    if any(keyword in lowered for keyword in _COMPLEX_KEYWORDS):
        return RouteDecision(
            route=RouteType.COMPLEX_TASK,
            need_retrieval=has_documents,
            need_planning=True,
            reason="降级启发式：命中多步任务关键词",
            confidence=0.5,
        )

    if has_documents and any(keyword in lowered for keyword in _RETRIEVAL_KEYWORDS):
        return RouteDecision(
            route=RouteType.DOCUMENT_RETRIEVAL,
            need_retrieval=True,
            reason="降级启发式：问题指向用户文档",
            confidence=0.5,
        )

    return RouteDecision(
        route=RouteType.DIRECT,
        need_retrieval=False,
        reason="降级启发式：未命中检索或规划特征",
        confidence=0.4,
    )


def _format_memory(memory_context: list[MemoryItem] | None, limit: int = 5) -> str:
    if not memory_context:
        return "（无）"
    lines = [f"- [{item.tier.value}] {item.content}" for item in memory_context[:limit]]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 路由器
# --------------------------------------------------------------------------- #
class LLMRouter:
    """用一次 LLM 调用做三分类。实现 `app.core.interfaces.Router`。"""

    def __init__(self, llm: LLM, temperature: float = 0.0) -> None:
        self.llm = llm
        self.temperature = temperature
        # 最近一次路由消耗的 token / 成本，由 Orchestrator 读取后累加
        self.last_usage = Usage()

    def route(
        self,
        question: str,
        has_documents: bool = True,
        memory_context: list[MemoryItem] | None = None,
        history: list[ConversationTurn] | None = None,
    ) -> RouteDecision:
        self.last_usage = Usage()

        history_text = format_history(history)
        # 历史为空时 prompt 与扩展前逐字一致，保证 RQ1 基线可比
        if history_text:
            user_prompt = (
                f"用户是否有可检索的文档：{'是' if has_documents else '否'}\n"
                f"用户长期记忆：\n{_format_memory(memory_context)}\n\n"
                f"{history_text}\n\n"
                f"用户请求：{question}"
            )
        else:
            user_prompt = (
                f"用户是否有可检索的文档：{'是' if has_documents else '否'}\n"
                f"用户长期记忆：\n{_format_memory(memory_context)}\n\n"
                f"用户请求：{question}"
            )

        try:
            response = self.llm.generate(
                [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=self.temperature,
                json_mode=True,
            )
            self.last_usage = response.usage
        except Exception as exc:  # noqa: BLE001 - 路由失败不能让整个请求失败
            decision = _heuristic_route(question, has_documents)
            decision.reason = f"{decision.reason}（模型调用失败：{type(exc).__name__}）"
            return decision

        try:
            payload = _extract_json_object(response.text)
            route = _as_route(payload.get("route"))
            route, need_retrieval, need_memory, need_planning = _normalise(
                route,
                _as_bool(payload.get("need_retrieval")),
                _as_bool(payload.get("need_memory")),
                _as_bool(payload.get("need_planning")),
                has_documents,
            )
            reason = str(payload.get("reason") or "").strip()
            return RouteDecision(
                route=route,
                need_retrieval=need_retrieval,
                need_memory=need_memory,
                need_planning=need_planning,
                need_web_search=_as_bool(payload.get("need_web_search")),
                reason=reason or "模型未给出理由",
                confidence=_as_confidence(payload.get("confidence")),
            )
        except Exception as exc:  # noqa: BLE001 - 解析失败同样走降级
            decision = _heuristic_route(question, has_documents)
            decision.reason = f"{decision.reason}（模型输出无法解析：{type(exc).__name__}）"
            return decision


class RuleBasedRouter:
    """纯关键词路由器。

    保留它有两个用途：
      1. 作为 LLMRouter 降级逻辑的对照实现；
      2. 在评测里当「无 LLM 成本的基线」，用于对比"用模型做路由是否值得"。
    """

    def __init__(self) -> None:
        self.last_usage = Usage()

    def route(
        self,
        question: str,
        has_documents: bool = True,
        memory_context: list[MemoryItem] | None = None,
        history: list[ConversationTurn] | None = None,
    ) -> RouteDecision:
        # 规则路由刻意忽略 history：关键词匹配只看当前问题，
        # 否则历史里的"比较""报告"会污染当前轮的分类（Issue #44）。
        return _heuristic_route(question, has_documents)
