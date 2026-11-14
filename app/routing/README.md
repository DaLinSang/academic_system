# app/routing — 请求路由

## 1. 模块职责

判断一次用户请求应该走哪条路径。这是 Proposal RQ1「agent 能否选对信息来源」的直接实现。

**负责**：三分类决策、决策理由生成、模型不可用时的降级。

**不负责**：执行检索（`app/retrieval`）、拆解任务（`app/planning`）、编排流程（`app/agent`）。

## 2. 负责人

- 实现：A
- 验证：B、C

## 3. 对外公共接口

实现 `app.core.interfaces.Router`：

```python
class LLMRouter:
    def __init__(self, llm: LLM, temperature: float = 0.0) -> None: ...
    def route(self, question: str, has_documents: bool = True,
              memory_context: list[MemoryItem] | None = None) -> RouteDecision: ...
    last_usage: Usage          # 本次路由消耗的 token / 成本
```

另提供 `RuleBasedRouter`（纯关键词，无 LLM 成本），用于：
1. 作为降级逻辑的对照；
2. 评测中当「无模型成本的基线」，回答"用模型做路由是否值得"。

## 4. 输入

| 参数 | 类型 | 说明 |
|---|---|---|
| `question` | `str` | 用户原始问题 |
| `has_documents` | `bool` | 是否有可检索文档。为 `False` 时不可能走文档检索 |
| `memory_context` | `list[MemoryItem] \| None` | 已读取的长期记忆，会拼进提示词 |

## 5. 输出

`RouteDecision`：

| 字段 | 说明 |
|---|---|
| `route` | `direct` / `document_retrieval` / `complex_task` |
| `need_retrieval` | 归一化后：direct 恒为 False，document_retrieval 恒为 True |
| `need_planning` | 仅 complex_task 为 True |
| `need_memory` | 模型判断，供 RQ2 分析（记忆实际在路由前已读取） |
| `reason` | 一句中文依据，写进实验日志 |
| `confidence` | 0–1，已裁剪 |

## 6. 依赖

- `app.core.interfaces.LLM`（由 `app/llm/client.py` 提供）
- `app.core.types` 的 `RouteDecision` / `RouteType` / `Usage`

## 7. 配置

无需额外配置。使用 `.env` 里的模型配置，由 `get_llm()` 决定走 DeepSeek 还是港大 API。

## 8. 最小运行示例

```python
from app.llm.client import get_llm
from app.routing.router import LLMRouter

router = LLMRouter(get_llm())
decision = router.route("比较 RAG 和 ReAct 的方法差异")
print(decision.route, decision.reason, decision.confidence)
```

接入完整链路：

```python
from app.agent.factory import build_orchestrator
from app.core.mocks import MockPlanner, MockRetriever, MockMemoryStore, \
    MockMemoryManager, MockVerifier, MockAnswerGenerator

orch = build_orchestrator(
    router=LLMRouter(get_llm()),
    planner=MockPlanner(), retriever=MockRetriever(),
    memory_store=MockMemoryStore(), memory_manager=MockMemoryManager(),
    verifier=MockVerifier(), answer_generator=MockAnswerGenerator(),
    llm=get_llm(),
)
print(orch.run("讲义里 RAG 是什么").route)
```

## 9. 测试命令

```bash
uv run pytest tests/test_router.py -v
```

覆盖 25 项：接口一致性、JSON 解析（围栏 / 夹带解释 / 括号内含花括号）、
别名映射、三类归一化约束、置信度裁剪、布尔字符串强制转换、
四种降级路径、用量回收、记忆注入提示词。

## 10. 正常输出示例

```
RouteDecision(
    route=<RouteType.COMPLEX_TASK: 'complex_task'>,
    need_retrieval=True, need_memory=False, need_planning=True,
    reason='用户要求比较两篇论文的方法差异',
    confidence=0.86,
)
```

## 11. 常见错误及处理方法

| 现象 | 原因 | 处理 |
|---|---|---|
| `reason` 里出现「模型输出无法解析」 | 模型返回了非 JSON 文本 | 已自动降级为关键词判断；若频繁出现，检查提示词是否被改动 |
| `reason` 里出现「模型调用失败」 | 网络 / 鉴权 / 限流 | 检查 `.env` 的 key 与 `check_env.py`；已自动降级 |
| 同一问题两次路由结果不同 | 温度不为 0 | 构造函数固定 `temperature=0.0`，不要改 |
| `last_usage.total_tokens` 为 0 | 用的是 Mock 或 `RuleBasedRouter` | 正常，它们不调模型 |
| 明明有文档却路由到 direct | `has_documents=False` 被误传 | 检查 Orchestrator 调用处 |

## 12. 当前限制

- 只有三分类，未支持 `web_search` 的独立路由（`need_web_search` 字段已预留但恒为 False）
- 提示词为手工编写 + 少量规则，未做 few-shot 示例集
- 单次 LLM 调用，没有「先粗判再确认」的两阶段设计
- 未在多轮对话中利用 `turn_index`（当前无状态）

## 13. 最近一次验收状态

- 日期：2026-11-13
- 验收人：待 B、C 复核
- 结论：自测通过（25 项测试 + 全量 87 项回归 + ruff 全过）
