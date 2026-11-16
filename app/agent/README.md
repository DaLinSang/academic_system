# app/agent — 编排层

## 1. 模块职责

把各个模块拼成一条可运行的流水线，并保证任何单点失败都不会让整个请求崩溃。

**负责**：流程编排、状态传递、错误降级、用量归集、回答生成、依赖装配。

**不负责**：检索（`app/retrieval`）、校验（`app/verification`）、记忆存取（`app/memory`）、路由判断（`app/routing`）。

## 2. 负责人

- 实现：A
- 验证：B、C

## 3. 对外公共接口

### Orchestrator

```python
class Orchestrator:
    def __init__(self, *, router, planner, retriever, memory_store,
                 memory_manager, verifier, answer_generator, llm, top_k=5): ...
    def run(self, question, *, session_id="default", user_id="default",
            turn_index=0, enable_routing=True, enable_retrieval=True,
            enable_memory=True, enable_verification=True) -> AgentState: ...
```

唯一入口是 `run()`，返回完整 `AgentState`。调用方（Streamlit / 评测脚本）只依赖它。

### AnswerGenerator

```python
class LLMAnswerGenerator:
    def __init__(self, llm, temperature=0.0,
                 max_evidence=6, max_chars_per_evidence=700): ...
    def generate(self, state: AgentState) -> str: ...
```

### 工厂

| 函数 | 用途 |
|---|---|
| `build_orchestrator(...)` | 显式注入所有依赖 |
| `build_mock_orchestrator()` | 全 Mock，无需 key 与数据库 |
| `build_stage1_orchestrator(top_k=5, memory_strategy="llm")` | 真实 LLM / 检索 / MemoryStore 装配；需要 PostgreSQL 与 Ollama |

`build_stage1_orchestrator` 各模块运行时实际走的文件：

| 角色 | 运行时实现 | 文件 |
|---|---|---|
| Router | `LLMRouter` | `app/routing/router.py` |
| Planner | `LLMPlanner` | `app/planning/planner.py` |
| Retriever | `HybridRetriever`（B） | `app/retrieval/` |
| MemoryStore | `PostgresMemoryStore`（B） | `app/memory/postgres_store.py` |
| MemoryManager | `LLMMemoryManager`（A） | `app/memory/manager.py` |
| Verifier | `LexicalVerifier`（C） | `app/verification/` |
| AnswerGenerator | `LLMAnswerGenerator` | `app/agent/answer_generator.py` |

`memory_strategy="rule"` 会把 MemoryManager 换成 `app/memory/policy.py` 的规则版
（B 维护，默认不启用，仅作 RQ2 消融基线）。

## 4. 输入

`Orchestrator.run()` 的入参见上。`AnswerGenerator.generate()` 读取 `AgentState` 的：
`question`、`evidence`、`route`、`memory_context`。

## 5. 输出

`AgentState`。关键字段：

| 字段 | 由谁写 |
|---|---|
| `route` / `plan` | Router / Planner |
| `evidence` | Retriever（经 `add_evidence` 去重） |
| `tool_results` | 每次工具调用记一条（`ToolResult`：成功与否、耗时、查询、命中数） |
| `plan[].status` / `.result_summary` | 执行后回填；未执行的 `llm` 步骤保持 `pending` |
| `current_step` | 已处理（非 pending）的步骤数 |
| `draft_answer` / `citations` | AnswerGenerator + `build_citations()` |
| `claims` | Verifier |
| `final_answer` | `_compose_final()` |
| `usage` / `trace` / `errors` | 各阶段累计 |

## 6. 依赖

- `app.core.interfaces` 的 8 个 Protocol（全部通过构造注入）
- `app.llm.client.get_llm()`（由调用方传入，本模块不自己创建）

## 7. 配置

无。`top_k` 由构造参数控制，默认 5。

## 8. 最小运行示例

```python
from app.agent.factory import build_mock_orchestrator

state = build_mock_orchestrator().run("讲义里 RAG 是什么？")
print(state.final_answer)
print(state.citations)
```

接入真实回答生成（其他仍是 Mock）：

```python
from app.agent.factory import build_orchestrator
from app.agent.answer_generator import LLMAnswerGenerator
from app.llm.client import get_llm
from app.core.mocks import (MockRouter, MockPlanner, MockRetriever,
                            MockMemoryStore, MockMemoryManager, MockVerifier)

orch = build_orchestrator(
    router=MockRouter(), planner=MockPlanner(), retriever=MockRetriever(),
    memory_store=MockMemoryStore(), memory_manager=MockMemoryManager(),
    verifier=MockVerifier(), answer_generator=LLMAnswerGenerator(get_llm()),
    llm=get_llm(),
)
print(orch.run("讲义里 Self-RAG 怎么决定要不要检索").final_answer)
```

## 9. 测试命令

```bash
uv run pytest tests/test_contracts.py tests/test_answer_generator.py -v
```

`test_contracts.py` 守编排层（四模式、降级、B1/B2 隔离），
`test_answer_generator.py` 守回答生成（18 项）。

## 10. 正常输出示例

```
[1] 来源：COMP7608B_Lec04.pdf 第 9 页
Self-RAG 通过 reflection token 判断是否需要检索 [1]。

⚠️ 以下内容在已检索到的证据中没有找到支持，请谨慎采信：
  - 该方法在三个数据集上取得最优结果
```

## 11. 常见错误及处理方法

| 现象 | 原因 | 处理 |
|---|---|---|
| 答案全是「资料不足」 | 检索没返回证据 | 查 `state.evidence` 与 `state.errors`；确认文档已导入 |
| 答案里出现 `[7]` 但只有 3 条证据 | 模型编造引用 | 查 `trace` 里 `citation_audit` 的 `invalid` 字段，这是 precision 失败样本 |
| `usage.total_tokens` 偏低 | 有组件调了模型但没归集 | 组件需提供 `last_usage` 属性，或直接写 `state.usage` |
| 某阶段异常但 `errors` 为空 | 该阶段内部吞了异常 | 内部捕获时必须 `state.errors.append(...)`，参考 `_run_retrieval` |
| 四种模式结果一样 | `MODE_PRESETS` 配错 | 确认 B1 的 `enable_retrieval=False` |

## 12. 当前限制

- Planner 仍为 Mock（固定四步），`complex_task` 的多步执行尚不真实
- 没有 ReAct 式的"观察-再决策"循环，检索只执行一轮
- `web_search` 与代码执行工具未接入
- 记忆写回依赖 `MemoryManager`，运行时默认是 `LLMMemoryManager`（`app/memory/manager.py`）
- 阶段 1 的真实装配：`PostgresMemoryStore`（B，存储）+ `LLMMemoryManager`（A，策略）
- 规则版 `RuleBasedMemoryManager`（`app/memory/policy.py`）保留但不启用，仅供 RQ2 消融；
  切换方式 `build_stage1_orchestrator(memory_strategy="rule")`
- 单轮对话，`turn_index` 未参与上下文构造

## 13. 最近一次验收状态

- 日期：2026-11-13
- 验收人：待 B、C 复核
- 结论：自测通过。本分支 80 项测试全绿 + ruff 全过；
  与 `feat/routing-llm-router` 合并后为 105 项。
