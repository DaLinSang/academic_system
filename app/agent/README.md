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
            turn_index=0, history=None, enable_routing=True, enable_retrieval=True,
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

### RunLogger

```python
class RunLogger:
    def __init__(self, *, mode, flags=None, dataset_name="unspecified",
                 dataset_size=None, model=None, top_k=5,
                 name=None, runs_dir=None, extra=None): ...
    def log(self, state: AgentState) -> None: ...
    def finish(self, summary: dict | None = None) -> Path: ...
```

一次实验运行 = 一个目录（`runs/<run_id>/`），内含 `meta.json` 与 `samples.jsonl`。

**只负责落盘，不算指标。** 指标由成员 C 在 `app/evaluation/` 里实现，
口径见 `docs/eval-contract.md`。

### SessionStore（用户聊天存储）

```python
class SessionStore:
    def __init__(self, base_dir: Path = Path("runs/sessions")): ...
    def append(self, state: AgentState, *, user_id=None, session_id=None) -> Path: ...
    def load(self, user_id, session_id) -> list[dict]: ...
    def restore(self, user_id, session_id) -> list[AgentState]: ...
    def list_sessions(self, user_id) -> list[SessionSummary]: ...
    def clear(self, user_id, session_id) -> bool: ...
```

落盘位置：`runs/sessions/<user_id>/<session_id>.jsonl`（追加式）。

**SessionStore vs RunLogger 边界（Issue #32 A 部分）**：

| | RunLogger | SessionStore |
|---|---|---|
| 用途 | 实验记录（评测跑批） | 用户聊天历史（Streamlit 会话） |
| 目录 | `runs/<run_id>/` | `runs/sessions/<user>/<session>.jsonl` |
| 谁写 | 评测脚本显式调用 | UI 每轮调用 |
| 可变性 | 固定不可变，保证可复现 | 可清空、可删除 |
| 内容 | 一次实验 N 个模式的完整 AgentState | 一轮问答 + 可复查的 AgentState 子集 |

**两者不混用**：实验数据要稳定可复现，聊天历史要能删能改，用途相反。

**恢复与 turn_index 连续性**：
- `restore()` 读回的每轮带原本的 `turn_index`；
- 下一轮 `turn_index = len(恢复的历史)`（从 0 起、连续递增）；
- 清空会话后从 0 重新开始；
- `ConversationTurn` 只带 question/answer/turn_index，完整状态在 JSONL 里。

**用户/工作区范围（Issue #32 决策）**：
本阶段只按 `user_id` 隔离长期记忆与会话（见 B 的 #58），**不做 workspace**——
PDF 索引全局共享，检索不带 workspace 维度。理由：RQ1–RQ4 不依赖 workspace，
加了是研究范围外的复杂度。若将来要做，需另开 `!` 契约 PR 改 `Retriever.retrieve`
与相关数据结构，并经三人同意。

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
uv run pytest tests/test_session_boundary_a32.py -v   # Issue #32：会话边界与 turn_index 连续
```

`test_contracts.py` 守编排层（四模式、降级、B1/B2 隔离），
`test_answer_generator.py` 守回答生成，`test_session_boundary_a32.py` 守 SessionStore
与 RunLogger 目录边界、恢复后 turn_index 连续、会话/用户隔离、敏感信息脱敏。

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

当前仍存在的边界（如实记录，供报告"局限性"章节引用）：

- Planner 生成的步骤中，`llm` 类步骤由 `_maybe_replan` 统一处理，不做逐步 ReAct 循环；
- 检索失败会降级（向量挂→BM25、无证据→拒答），但**没有自动重试换策略的恢复循环**；
- 网页搜索已接入（Issue #40/#41），但搜索后端是 DuckDuckGo HTML 端点，HTML 结构变化会降级到搜索摘要；
- 记忆写回依赖 `MemoryManager`，运行时默认是 `LLMMemoryManager`（`app/memory/manager.py`）；
  规则版 `RuleBasedMemoryManager`（`app/memory/policy.py`）保留但不启用，仅供 RQ2 消融；
  切换方式 `build_stage1_orchestrator(memory_strategy="rule")`
- 多轮上下文（Issue #44）已实现：`run(history=...)` 把历史送进 Router/Planner/AnswerGenerator，
  模型能理解"它/这个"指代；历史只用于消解指代，不是事实来源，事实仍只取自证据
- 工作区（workspace）隔离本阶段不做：记忆/会话按 `user_id` 隔离，PDF 全局共享（见上文决策）
- **实验影响**：开启历史后 B1–B4 输出会变，正式实验必须在功能冻结后统一跑，边改边跑的数据作废

## 13. 最近一次验收状态

- 日期：2026-11-19
- 验收人：A 自测；B/C 在 PR 上复核
- 结论：全量测试通过（含 #32 会话边界 7 项、#44 多轮 21 项），ruff 全过。
  已交付：路由/规划/生成/校验/记忆/网页搜索/多轮上下文/会话持久化；
  下一节点是 C 的评测标注集与三次正式实验（Issue #49）。

## 14. 学习问题的证据预算与页码核对（#78 / #79）

`Orchestrator` 用 B 的 `is_study_request()` 识别整篇概览、复习提纲和文献比较。普通问题继续用所设 `top_k`；学习问题调用 Retriever 时至少取 6 条。Planner 拆分出多个检索步骤后，在生成前把不同文件、不同页的证据优先排到前六条。显式指定的文件范围与来源澄清仍由 Retriever 决定。

`LLMAnswerGenerator` 对学习问题要求逐主题归纳、比较时分别引述两份材料；若两份材料主题不同，不把单方观点写成双方共同点。引用 `[n]` 是证据编号，不是 PDF 页码。若模型紧跟引用写了错误页码（如 `第6页 [6]` 但 `[6]` 实际来自第9页），生成模块按对应证据纠正，并在 trace 记录 `page_citation_correction`。没有紧邻有效引用的页码仍需人工核查。

这一改动没有改 `AgentState`、公共 Protocol 或数据库结构。详情见 [测试记录](../../docs/b-study-overview-validation-2026-11-22.md)。
