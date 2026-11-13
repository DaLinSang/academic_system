"""任务规划器（A 负责）。

Planner 只做一件事：**把复杂请求拆成可执行步骤，然后交出去**。
它不执行任何工具——执行是 Orchestrator 的事。这个边界必须守住，
否则 Planner 会长成一个第二编排器，两条执行路径迟早打架。

什么时候会被调用
----------------
只有 `RouteDecision.need_planning=True`（即 complex_task）时才调用。
其他两类返回空列表，表示"不需要拆解"。

拆出来的步骤怎么被用
--------------------
Orchestrator 只看两件事：
  1. 所有 `tool == "retrieval"` 且有 `query` 的步骤 → 分别检索，结果合并去重；
  2. 其余步骤目前不单独执行（LLM 分析在 AnswerGenerator 里一次完成）。

也就是说，**当前版本里 `tool == "llm"` 的步骤只起"说明意图"的作用**，
用来让计划可读、可评测，不产生额外的模型调用。这是有意的取舍：
多轮 ReAct 循环留到后续迭代，先把可复现的单轮流程做稳。

设计要点
--------
1. `temperature=0`，保证同一问题两次拆解结果一致，否则消融实验不可比。
2. 步骤编号由本模块**重新排**成 1..n，不信任模型给的编号——模型经常从 0 开始、
   或者跳号，直接透传会让 `depends_on` 全部失效。
3. 检索步骤没有 `query` 时补上原问题，否则 Orchestrator 会退化成"用整句检索"，
   等于这一步白拆。
4. 解析失败 → 返回最小两步计划（检索 + 汇总），绝不返回空列表。
   空列表会让 complex_task 静默退化成普通检索，实验里看不出来。
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.core.interfaces import LLM
from app.core.types import MemoryItem, PlanStep, RouteDecision, RouteType, Usage

__all__ = ["LLMPlanner", "fallback_plan"]

_ALLOWED_TOOLS = {"retrieval", "memory", "web_search", "llm", "none"}

_FENCE = re.compile(r"```(?:json)?\s*(.+?)```", re.DOTALL)

_SYSTEM_PROMPT = """你是学术助手的任务规划器。把用户的复杂请求拆成可执行步骤，输出 json 数组。

规则：
1. 每一步只做一件事，步骤之间不要重叠。
2. tool 只能是 retrieval / llm / none 之一：
   - retrieval：需要查阅用户文档。必须给出具体的 query，不要把整句问题原样抄进去。
   - llm：基于已有信息做分析、比较、归纳或写作。
   - none：占位或人工步骤。
3. 第一个需要查资料的步骤用 retrieval，后续分析步骤用 llm。
4. 总步数控制在 2 到 5 步，不要为了凑数拆出无意义的步骤。
5. description 用一句中文，具体说明这一步产出什么，不要写"处理一下"这种空话。
6. depends_on 填所依赖步骤的序号列表，没有依赖填空数组。

只输出下面这种 json 数组，不要输出任何其他内容：
[{"step_id": 1, "description": "检索两篇论文中关于检索增强的段落", "tool": "retrieval", "query": "RAG 检索增强 方法", "depends_on": []}]"""


# --------------------------------------------------------------------------- #
# 解析工具
# --------------------------------------------------------------------------- #
def _extract_json_array(text: str) -> list[Any]:
    """从模型输出里抠出第一个完整的 JSON 数组。

    与 router 里的对象提取逻辑同源，但这里目标类型是数组。
    后续如果第三个模块也需要，应抽到 `app/core` 下统一维护。
    """
    raw = text.strip()

    fenced = _FENCE.search(raw)
    if fenced:
        raw = fenced.group(1).strip()

    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        pass

    start = raw.find("[")
    if start == -1:
        raise ValueError("模型输出里没有 JSON 数组")

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
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                parsed = json.loads(raw[start : index + 1])
                if isinstance(parsed, list):
                    return parsed
                raise ValueError("JSON 顶层不是数组")

    raise ValueError("JSON 方括号不配对")


def _as_tool(value: Any) -> str:
    tool = str(value).strip().lower()
    return tool if tool in _ALLOWED_TOOLS else "none"


def _normalise_steps(raw_steps: list[Any], question: str, max_steps: int) -> list[PlanStep]:
    """把模型给的任意结构整理成合法的 PlanStep 列表。

    关键动作：
      - 重新编号为 1..n（模型的编号不可信：常从 0 开始或跳号）
      - **按原编号建立映射**再翻译 depends_on，否则重排后依赖关系全部失效
      - 检索步骤缺 query 时补原问题
      - 丢掉描述为空的步骤
    """
    collected: list[dict[str, Any]] = []

    for item in raw_steps:
        if not isinstance(item, dict):
            continue
        description = str(item.get("description") or "").strip()
        if not description:
            continue

        tool = _as_tool(item.get("tool"))
        query = item.get("query")
        query = str(query).strip() if query else None
        if tool == "retrieval" and not query:
            query = question

        collected.append(
            {
                "orig_id": item.get("step_id"),
                "description": description,
                "tool": tool,
                "query": query,
                "depends_on": item.get("depends_on"),
            }
        )
        if len(collected) >= max_steps:
            break

    # 原编号 → 新编号
    id_map: dict[int, int] = {}
    for new_id, entry in enumerate(collected, start=1):
        try:
            id_map[int(entry["orig_id"])] = new_id
        except (TypeError, ValueError):
            continue

    steps: list[PlanStep] = []
    for new_id, entry in enumerate(collected, start=1):
        raw_depends = entry["depends_on"]
        depends: list[int] = []
        if isinstance(raw_depends, list):
            for dep in raw_depends:
                try:
                    mapped = id_map.get(int(dep))
                except (TypeError, ValueError):
                    mapped = None
                if mapped and mapped != new_id and mapped not in depends:
                    depends.append(mapped)

        steps.append(
            PlanStep(
                step_id=new_id,
                description=entry["description"],
                tool=entry["tool"],  # type: ignore[arg-type]
                query=entry["query"],
                depends_on=depends,
            )
        )
    return steps


def fallback_plan(question: str) -> list[PlanStep]:
    """最小可用计划：先检索，再汇总。

    故意不返回空列表——空列表会让 complex_task 静默退化成普通检索，
    实验里完全看不出 Planner 挂了。
    """
    return [
        PlanStep(
            step_id=1,
            description="检索与问题相关的文档片段",
            tool="retrieval",
            query=question,
        ),
        PlanStep(
            step_id=2,
            description="基于检索结果组织带引用的回答",
            tool="llm",
            depends_on=[1],
        ),
    ]


def _format_memory(memory_context: list[MemoryItem] | None, limit: int = 5) -> str:
    if not memory_context:
        return "（无）"
    return "\n".join(f"- [{item.tier.value}] {item.content}" for item in memory_context[:limit])


# --------------------------------------------------------------------------- #
# 规划器
# --------------------------------------------------------------------------- #
class LLMPlanner:
    """用一次 LLM 调用生成多步计划。实现 `app.core.interfaces.Planner`。"""

    def __init__(self, llm: LLM, temperature: float = 0.0, max_steps: int = 5) -> None:
        if max_steps < 2:
            raise ValueError("max_steps 至少为 2，单步计划没有拆解意义")
        self.llm = llm
        self.temperature = temperature
        self.max_steps = max_steps
        self.last_usage = Usage()

    def plan(
        self,
        question: str,
        route: RouteDecision,
        memory_context: list[MemoryItem] | None = None,
    ) -> list[PlanStep]:
        # 只有 complex_task 才拆解。其余两类返回空表示"不需要计划"。
        if route.route != RouteType.COMPLEX_TASK:
            self.last_usage = Usage()
            return []

        self.last_usage = Usage()

        user_prompt = (
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
        except Exception:  # noqa: BLE001 - 规划失败不能让请求失败
            return fallback_plan(question)

        try:
            raw_steps = _extract_json_array(response.text)
            steps = _normalise_steps(raw_steps, question, self.max_steps)
        except Exception:  # noqa: BLE001
            return fallback_plan(question)

        return steps or fallback_plan(question)
