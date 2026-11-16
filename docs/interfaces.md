# 接口契约（阶段 0 冻结版 v0.1）

**这份文档是 A 交给 B 和 C 的交接单。** 冻结之后：

- 你只需要实现自己那几个方法，其他模块的进度与你无关；
- 用 `app/core/mocks.py` 里的 Mock 就能跑通整条链路；
- 接口要改 → 在群里说，三人确认后由 A 改 `app/core/interfaces.py` 并通知。

---

## 0. 铁律

1. **不许直接读 `os.environ`**，一律用 `app.core.config.settings`。
2. **不许直接 `import openai`**，一律用 `app.core.interfaces.LLM`。
3. **不许在模块里自定义重复的数据结构**，一律用 `app.core.types`。
4. **不许跨模块直接操作对方的数据库/文件**，只能通过接口方法。
5. 跨模块抛的异常必须转成 `app/core/errors.py` 里的类型。

---

## 1. 数据流全景

```
                    ┌──────────── AgentState（唯一状态载体）────────────┐
                    │                                                   │
question ──► MemoryStore.search ──► Router ──► Planner ──► Retriever   │
                    │                 │           │            │       │
                    │                 ▼           ▼            ▼       │
                    │            RouteDecision  PlanStep    Evidence   │
                    │                                          │       │
                    │                                          ▼       │
                    │                              AnswerGenerator     │
                    │                                          │       │
                    │                                          ▼       │
                    │                                      draft_answer  │
                    │                                          │       │
                    │                                          ▼       │
                    │                                      Verifier      │
                    │                                          │       │
                    │                                          ▼       │
                    │                                     ClaimCheck[]   │
                    │                                          │       │
                    │                            MemoryManager ─┘       │
                    │                                   │                │
                    └───────────────────────────────────┴────────────────┘
                                                        ▼
                                          MemoryStore.add(MemoryCandidate)
```

---

## 2. 成员 B：Retriever

**文件**：`app/retrieval/`（实现放这里，接口在 `app/core/interfaces.py:Retriever`）

```python
class Retriever(Protocol):
    def retrieve(self, query: str, top_k: int = 5) -> list[Evidence]: ...
    def index_document(self, pdf_path: str, document_id: str | None = None) -> int: ...
```

| 项 | 要求 |
|---|---|
| 输入 | 自然语言 query（中文/英文都要能处理） |
| 输出 | `list[Evidence]`，按 score 降序；**必须带 `file_name` 和 `page_number`** |
| 无命中 | 返回 `[]`，**不要抛异常** |
| 服务不可用 | 抛 `RetrievalError` |
| `index_document` | 返回切出的 chunk 数；同一 PDF 重复导入要幂等 |

`Evidence.score` 的语义：**只保证"越大越相关"**。向量余弦分和 RRF 分不可跨方法比较，
所以 `retrieval_method` 字段必须如实填写。

**最小可用实现骨架**：

```python
from app.core.errors import RetrievalError
from app.core.types import Evidence, RetrievalMethod

class PgVectorRetriever:
    def __init__(self, dsn: str, embedder): ...
    def retrieve(self, query: str, top_k: int = 5) -> list[Evidence]:
        try:
            ...
        except Exception as exc:
            raise RetrievalError(str(exc)) from exc
    def index_document(self, pdf_path: str, document_id: str | None = None) -> int:
        ...
```

**验收（A 会这样测你）**：
1. 给一个问题，能拿到 `Evidence`，`file_name` 和 `page_number` 都在；
2. 问一个文档里根本没有的东西，返回 `[]` 而不是报错；
3. 把数据库停掉，抛的是 `RetrievalError` 而不是 `psycopg.Error`；
4. `AnswerGenerator` 能直接吃你的输出，不用做任何转换。

---

## 3. 成员 B：MemoryStore + MemoryManager

**文件**：`app/memory/`

本模块由**两个接口**组成，都归成员 B：

| 接口 | 实现文件 | 职责 |
|---|---|---|
| `MemoryStore` | `postgres_store.py` | 怎么存、怎么查、怎么删（存储层） |
| `MemoryManager` | `policy.py` | 要不要存、存什么（策略层，产出 `MemoryCandidate`） |

A 负责审核策略口径与后续迭代，但**不在本模块内另写实现**。

```python
class MemoryStore(Protocol):
    def add(self, candidate: MemoryCandidate, user_id: str = "default") -> MemoryItem: ...
    def search(self, query: str, user_id: str = "default",
               tiers: list[MemoryTier] | None = None, top_k: int = 5) -> list[MemoryItem]: ...
    def update(self, memory_id: str, content: str | None = None,
               importance: float | None = None) -> MemoryItem | None: ...
    def delete(self, memory_id: str) -> bool: ...
    def list_all(self, user_id: str = "default") -> list[MemoryItem]: ...
```

**注意分工**：`MemoryStore` 与 `MemoryManager` 都归成员 B。
「要不要存、存什么」由 `MemoryManager` 判断（`app/memory/policy.py`），
「怎么存、怎么查」由你实现（`app/memory/postgres_store.py`）。
A 只负责审核策略与后续迭代，不在本模块内另写实现。
`add()` 的入参是 `MemoryCandidate`，**不是** `MemoryItem` —— 因为 memory_id、时间戳由你生成。

| 项 | 要求 |
|---|---|
| `add` | 生成 `memory_id`（建议 `mem-<uuid8>`）与时间戳，返回完整 `MemoryItem` |
| `search` | 语义相似度 + recency + importance 加权（Proposal 4.3）；`tiers` 为 None 表示全部 |
| `update` | 不存在的 id 返回 `None`，不要抛 |
| `delete` | 返回是否真的删掉了 |
| 隔离 | 所有查询必须按 `user_id` 过滤，否则多用户会串记忆 |

**验收**：增删改查五个方法都有测试；删除后 `search` 不再返回；关闭记忆开关时 UI 不显示任何条目。

---

## 4. 成员 C：Verifier

**文件**：`app/verification/`

```python
class Verifier(Protocol):
    def verify(self, answer: str, evidence: list[Evidence]) -> list[ClaimCheck]: ...
```

| 项 | 要求 |
|---|---|
| 输入 | 草稿答案 + 已检索到的证据（可能为空） |
| 输出 | `list[ClaimCheck]`，每条含 claim / status / confidence / supporting_evidence / note |
| 空证据 | 全部判 `unsupported`，**不要抛异常** |
| `supporting_evidence` | 必须是 `evidence` 里真实存在的 `chunk_id`，UI 靠它定位页码 |
| 内部失败 | 返回降级结果（全 unsupported + note），让 Agent 仍能出答案 |

### ⚠️ 拆分 claim 时最容易踩的坑

不要按 `：` 拆，也不要保留引导语和结语。否则会出现：

```
✗ "根据检索到的文档："        → 被判 unsupported，虚高 unsupported 率
✗ "以上结论均可在页码中核对"   → 同上
✗ "[1]（Lec04.pdf 第 9 页）"   → 纯引用标记，不是 claim
```

这会**直接污染 RQ3 的实验结果**。`app/core/mocks.py:MockVerifier._split_claims` 里有一版
过滤规则（长度阈值 + 元语句正则），可以直接参考或复用。

**验收**：用已知页码的 Evidence 测——真支持的要判 supported，明显编造的（如"该论文在三个数据集上
取得最优结果"）要判 unsupported；证据为空时全部 unsupported 且不报错。

---

## 5. 成员 A 提供的接口（B/C 直接调用，不要自己实现）

### LLM 统一调用层

```python
class LLM(Protocol):
    def generate(self, messages: list[dict[str, str]], temperature: float = 0.0,
                 max_tokens: int | None = None, json_mode: bool = False) -> LLMResponse: ...
```

- 返回 `LLMResponse(text, model, usage, finish_reason)`
- `usage` 里含 token 与 cost —— **RQ4 的唯一数据来源**
- 失败抛 `LLMError`；不要在调用方做重试，重试在层内做

```python
from app.llm.client import get_llm
resp = get_llm().generate([{"role": "user", "content": "hi"}])
print(resp.text, resp.usage.total_tokens)
```

### Router / Planner

```python
router.route(question, has_documents=True, memory_context=None) -> RouteDecision
planner.plan(question, route, memory_context=None) -> list[PlanStep]
```

`Planner` **只生成步骤，不执行工具**。执行由 Orchestrator 负责。

### AnswerGenerator

```python
answer_generator.generate(state: AgentState) -> str
```

要求：答案里用 `[1] [2]` 标记引用，编号对应 `state.citations` 的顺序。

---

## 6. 怎么用 Mock 开发

```python
from app.agent.factory import build_mock_orchestrator

orch = build_mock_orchestrator()
state = orch.run("讲义里 RAG 是什么？")
print(state.final_answer)
```

只替换你负责的那一块：

```python
orch = build_mock_orchestrator()
orch.retriever = MyRealRetriever()      # B 替换检索
orch.verifier  = MyRealVerifier()       # C 替换校验
state = orch.run("...")                  # 其余代码一行不改
```

---

## 7. 变更记录

| 日期 | 版本 | 变更 | 影响 |
|---|---|---|---|
| 2026-11-03 | v0.1 | 初始冻结：7 个数据结构、5 个接口、B1–B4 开关 | 全员 |
