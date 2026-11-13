# app/planning — 任务规划

## 1. 模块职责

把复杂请求拆成可执行步骤。

**负责**：生成 `PlanStep` 列表、为检索步骤生成子查询、给出步骤依赖。

**不负责**：执行工具。Planner 只产出计划，执行由 `app/agent/orchestrator.py` 负责。
这个边界必须守住，否则会长成第二个编排器，两条执行路径迟早打架。

## 2. 负责人

- 实现：A
- 验证：B、C

## 3. 对外公共接口

实现 `app.core.interfaces.Planner`：

```python
class LLMPlanner:
    def __init__(self, llm: LLM, temperature: float = 0.0, max_steps: int = 5) -> None: ...
    def plan(self, question: str, route: RouteDecision,
             memory_context: list[MemoryItem] | None = None) -> list[PlanStep]: ...
    last_usage: Usage          # 本次规划消耗的 token / 成本
```

另有模块级函数 `fallback_plan(question) -> list[PlanStep]`，返回最小两步计划。

## 4. 输入

| 参数 | 说明 |
|---|---|
| `question` | 用户原始问题 |
| `route` | Router 的决策。**只有 `complex_task` 才会真的拆解**，其余返回 `[]` |
| `memory_context` | 长期记忆，会拼进提示词 |

## 5. 输出

`list[PlanStep]`，字段含义：

| 字段 | 说明 |
|---|---|
| `step_id` | 由本模块重排为 1..n，不信任模型给的编号 |
| `description` | 一句中文，说明这一步产出什么 |
| `tool` | `retrieval` / `llm` / `memory` / `web_search` / `none`，非法值归为 `none` |
| `query` | 仅 `retrieval` 步骤使用；缺失时补原问题 |
| `depends_on` | 依赖的步骤序号，已按新编号重新映射 |

Orchestrator 当前只消费 `tool == "retrieval"` 的步骤（逐条检索后合并去重）。
`tool == "llm"` 的步骤目前只起"说明意图"的作用，不产生额外模型调用。

## 6. 依赖

- `app.core.interfaces.LLM`（由 `app/llm/client.py` 提供）
- `app.core.types` 的 `PlanStep` / `RouteDecision` / `Usage`

## 7. 配置

无。`max_steps` 由构造参数控制，默认 5，最小 2。

## 8. 最小运行示例

```python
from app.llm.client import get_llm
from app.planning.planner import LLMPlanner
from app.core.types import RouteDecision, RouteType

route = RouteDecision(route=RouteType.COMPLEX_TASK, need_retrieval=True, need_planning=True)
steps = LLMPlanner(get_llm()).plan("比较 RAG 和 ReAct 的方法差异", route)

for step in steps:
    print(step.step_id, step.tool, step.description, "| query:", step.query)
```

## 9. 测试命令

```bash
uv run pytest tests/test_planner.py -v
```

21 项，覆盖：非复杂任务不消耗模型、JSON 围栏与夹带解释、编号重排与依赖重映射、
缺 query 补原问题、非法工具归为 none、空描述丢弃、自依赖清除、步数上限、
四种降级路径、用量归集、记忆注入。

## 10. 正常输出示例

```
1 retrieval 检索两篇论文中关于检索增强的段落 | query: RAG 检索增强 方法
2 llm       提取各自的训练目标              | query: None
3 llm       按方法维度对比并给出结论         | query: None
```

## 11. 常见错误及处理方法

| 现象 | 原因 | 处理 |
|---|---|---|
| `complex_task` 只返回 2 步 | 模型输出解析失败，走了 `fallback_plan` | 查 `trace` 里 `plan` 阶段的输出；确认 `json_mode` 生效 |
| 检索结果全是同一段 | 多个 retrieval 步骤的 query 重复 | 检查提示词；或在 `_normalise_steps` 里做 query 去重 |
| `depends_on` 全是空 | 模型没给依赖，或给了超出范围的编号 | 正常，当前执行器不使用依赖关系 |
| `last_usage` 为 0 | 非 complex_task，或用了 Mock | 正常 |
| 计划里有 6 步以上 | `max_steps` 被调大 | 默认 5，超过就截断 |

## 12. 当前限制

- 单轮规划，没有"执行后发现不对再重规划"的循环
- 不使用 `depends_on` 做拓扑排序，步骤按顺序线性执行
- 没有 few-shot 示例集，拆解质量依赖提示词
- `web_search` / `code_exec` 类步骤只能生成，无法执行

## 13. 最近一次验收状态

- 日期：2026-11-13
- 验收人：待 B、C 复核
- 结论：自测通过（本分支 83 项测试 + ruff 全过；
  与 routing / answer-generator 两支合并后为 126 项）
