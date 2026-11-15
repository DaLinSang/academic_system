# app/memory — 长期记忆存取

## 模块职责

本模块落实长期记忆的持久化、语义检索和 CRUD，并提供一套保守的候选筛选规则。按照小组分工，记忆策略的最终审核和后续迭代由 A 负责；这里的策略实现通过冻结的 `MemoryManager` 接口注入，便于 A 替换。

这里的「长期」指数据存于 PostgreSQL，退出 Python、重启 Agent 后仍可读取；不是把对话历史全部塞给模型。`MemoryItem` 是跨模块传递的数据对象；数据库中的 embedding 只用于搜索，不传给 Router 或回答生成器。

## 文件导航

| 文件 | 作用 |
|---|---|
| `postgres_store.py` | `PostgresMemoryStore` 的五个公共方法和 Ollama 向量生成 |
| `policy.py` | `RuleBasedMemoryManager`，决定本轮是否有可保存的用户陈述 |
| `schema.sql` | PostgreSQL 表、用户/tier 索引与 pgvector 索引 |
| `__init__.py` | 向其他模块导出两个实现类 |
| `tests/test_memory_store.py` | 数据库存取、跨实例读取、隔离、排序 |
| `tests/test_memory_policy.py` | 三类候选、敏感内容过滤、去重 |

## 数据流

```text
Orchestrator 开始处理问题
  → MemoryStore.search(question, user_id)
  → Ollama 把问题编码为向量
  → PostgreSQL 按 user_id 隔离候选记忆
  → 综合语义相关性、更新时间和 importance 排序
  → MemoryItem 列表进入 AgentState.memory_context

Orchestrator 生成回答后
  → RuleBasedMemoryManager.extract_candidates(question, answer, existing_memories)
  → 只把策略层批准的 MemoryCandidate 交给 MemoryStore.add()
  → Ollama 编码候选内容
  → PostgreSQL 保存正文和向量
```

## 实现与公共接口

- `PostgresMemoryStore` 实现 `app/core/interfaces.py` 中冻结的 `MemoryStore` Protocol。
- `OllamaMemoryEmbedder` 使用统一配置中的 `OLLAMA_BASE_URL`、`EMBEDDING_MODEL` 和 `EMBEDDING_DIM`。
- `schema.sql` 创建 `memories` 表和 pgvector 索引。用户资料、偏好和任务状态分别使用 `profile`、`preference`、`task_state` tier。
- 数据库异常和 embedding 服务异常统一转换为 `MemoryStoreError`。
- 每次读取都按 `user_id` 过滤，避免不同用户之间互相读取记忆。

五个公共方法的含义：

| 方法 | 输入 | 输出与行为 |
|---|---|---|
| `add(candidate, user_id)` | `MemoryCandidate`、用户 ID | 编码内容、保存、返回含 ID/时间戳的 `MemoryItem` |
| `search(query, user_id, tiers, top_k)` | 本次问题、用户 ID、可选类型、条数 | 返回相近的 `MemoryItem` 列表；空问题返回 `[]` |
| `update(memory_id, content, importance)` | 记忆 ID、可选新正文/重要性 | 返回更新的条目；ID 不存在返回 `None`；正文改变时重新计算向量 |
| `delete(memory_id)` | 记忆 ID | 删除成功返回 `True`，不存在返回 `False` |
| `list_all(user_id)` | 用户 ID | 列出该用户记忆，供管理界面使用 |

`search()` 先过滤相似度低于 0.3 的记忆，减少误召回，再综合向量相似度、更新时间衰减和候选的 `importance` 排序。默认权重为 0.6、0.2、0.2；近期记忆的 recency 分数按 30 天时间常数衰减。可在构造 `PostgresMemoryStore` 时调整阈值和权重。

## 与 Agent 的接口调用

`build_stage1_orchestrator()` 会注入真实的 `PostgresMemoryStore`。Orchestrator 仍只调用 `MemoryStore` Protocol 的方法，不依赖存储类内部实现。`build_mock_orchestrator()` 继续使用 `MockMemoryStore`，因此离线演示和单元测试不需要数据库。

记忆是否写入仍由注入的 `MemoryManager` 决定。真实装配使用 `RuleBasedMemoryManager`：只从用户本轮明确表达的背景、偏好、任务状态中提取短陈述；跳过常见联系方式、密钥等敏感信息，以及与已读记忆文字重复的内容。没有固定「每 N 轮存一次」；每轮都检查，只有有合格候选时才保存。A 可以在工厂中按同一接口替换为更强的策略。

例如「我偏好用中文回答」可以成为 `preference`；「请用中文解释 RAG」是针对本轮 RAG 问题的指令，不应被误存成长期偏好。当前规则仅对独立的「请用中文回答／回复」等通用回答方式指令，以及明确带长期倾向的表达创建偏好候选。规则更新不会自动删除数据库里此前已经误存的条目；用户或管理界面可通过 `list_all()` 找到 ID 后调用 `delete()` 清理。

### A：如何接入和替换策略

真实装配入口为 `app.agent.factory.build_stage1_orchestrator()`。工厂创建 `PostgresMemoryStore` 和 `RuleBasedMemoryManager`，然后通过 `build_orchestrator(...)` 注入。`Orchestrator.run()` 在路由前调 `memory_store.search()`；在回答完成后调 `memory_manager.extract_candidates()`，再把返回的候选逐条交给 `memory_store.add()`。A 无需直接查询 `memories` 表，也无需修改公共接口名称。

如需升级为 LLM 判断策略，只实现 `MemoryManager.extract_candidates(question, answer, existing_memories)` 并在工厂替换注入对象。当前规则只识别明确的中文表达，不能把几轮零散对话自动总结成新画像，也不会自动解决互相矛盾的偏好。当前 `existing_memories` 是搜索召回的 top-k，不是用户全部记忆，因此策略层的文字去重只覆盖这些已召回项。策略的最终选择请 A review。

### C：如何读取、管理和关闭

```python
from app.agent.factory import build_stage1_orchestrator

orch = build_stage1_orchestrator()
state = orch.run("请用中文解释 RAG", user_id="student-1", enable_memory=True)
print(state.memory_context)  # 本次回答前召回的 MemoryItem
print(state.errors)          # 记忆服务故障时，Orchestrator 会记录错误

# 管理页只调用公共 MemoryStore 接口，不碰数据库表。
items = orch.memory_store.list_all(user_id="student-1")
for item in items:
    print(item.memory_id, item.tier, item.content)
```

删除单条：`orch.memory_store.delete(memory_id)`。清空该用户：先 `list_all(user_id)` 再逐条 `delete(item.memory_id)`。当前冻结接口的 `delete()` 只收 ID，不附带 `user_id`；C 的界面应只允许用户操作其 `list_all(user_id)` 返回的 ID。长期记忆开关关闭时传 `enable_memory=False`，同一轮的读取和写入都会跳过。`B1/B2` 默认关闭记忆；`B3/B4` 默认开启。

Mock 演示使用 `build_mock_orchestrator()`；它注入 `MockMemoryStore`、`MockMemoryManager`，不会访问真实 PostgreSQL 或 Ollama。真实演示可用 `uv run python -m app.demo --real -m B4 -q "请用中文解释 RAG"`，其中 demo 的固定 `user_id` 是 `demo-user`。

## 本地运行前置条件

```bash
docker compose up -d
ollama pull nomic-embed-text
```

`ollama serve` 只有在 Ollama 服务尚未运行时才执行；如果 Ollama 桌面应用已经启动，再运行可能提示端口 `11434` 已占用。`ollama list` 能确认模型已下载；`curl --noproxy '*' http://localhost:11434/api/tags` 能检查本地 HTTP 服务。`ollama pull` 下载模型只需在第一次安装或模型缺失时执行，不是每次启动项目都运行。

`.env` 中可配置：

```dotenv
POSTGRES_HOST=localhost
POSTGRES_PORT=5434
POSTGRES_DB=academic
POSTGRES_USER=academic
POSTGRES_PASSWORD=academic_dev_pw
OLLAMA_BASE_URL=http://localhost:11434
EMBEDDING_MODEL=nomic-embed-text
EMBEDDING_DIM=768
```

真实装配创建 `PostgresMemoryStore` 时不连库；第一次读写时确保表结构存在。真正运行记忆读写时，数据库和 Ollama 必须可连接。Mock 装配不依赖这些服务。

Ollama 是运行在本机的模型服务。`nomic-embed-text` 是 embedding 模型：把一段文字变成 768 个数字；它不负责生成最终答案。Memory 在 `add()` 时编码候选内容，在 `search()` 时编码本次问题；Retrieval 在索引 PDF chunk 和检索 query 时也使用 embedding。最终回答由 A 的 LLM 层生成。

### Ollama 报错怎样排查

| 现象 | 常见原因 | 排查方式 |
|---|---|---|
| `Connection refused` | Ollama 服务未启动或端口不对 | `curl --noproxy '*' http://localhost:11434/api/tags`；检查 `OLLAMA_BASE_URL` |
| `model not found` | 模型尚未下载或模型名不一致 | `ollama list`；必要时 `ollama pull nomic-embed-text` |
| `502 Bad Gateway`，但上述 curl 返回 200 | 本机 HTTP 代理接管了 `localhost` 请求 | 本模块已对 Ollama 请求设置 `trust_env=False`，令其直连本地服务 |
| 向量维度错误 | 模型输出维度与数据库 `vector(768)` 不一致 | 确认 `EMBEDDING_MODEL`、`EMBEDDING_DIM` 与表结构一致 |
| `MemoryStoreError` 含数据库不可用 | Docker/PostgreSQL 未启动或连接参数错误 | `docker compose ps`，确认 postgres 为 `healthy` |

`502` 是 HTTP 请求在网关/代理一侧失败的信号，不能仅凭状态码断言 Ollama 服务本身坏了。本机实际排查发现：直连 Ollama 返回 200，而 Python 请求受到环境代理影响；对本地请求禁用读取代理环境后，真实 embedding 写入、重新实例化检索和删除均成功。

## 验证

在 PyCharm 项目终端运行：

```bash
docker compose ps
ollama list
uv run pytest tests/test_memory_store.py tests/test_memory_policy.py tests/test_factory.py -q
uv run pytest -q
uv run ruff check .
git diff --check
```

`test_memory_store.py` 使用临时 `user_id`，测试结束清理该用户创建的记录；它使用确定性的假向量，验证真实 PostgreSQL 的存取和排序，避免把 Ollama 状态混进数据库测试。真实 Ollama 另做端到端人工验证。数据库不可用时，这组集成测试会跳过，不能把 `skipped` 当成数据库已验证。

## 数据管理

Protocol 提供 `search`、`list_all`、`update` 和 `delete`。Web 界面的记忆查看、清空、关闭开关属于 UI/Agent 集成任务；目前接口没有批量清空方法，界面可以先通过 `list_all(user_id)` 获取条目并逐条调用 `delete(memory_id)`。关闭记忆时由调用方传 `enable_memory=False`，Orchestrator 会跳过记忆读取和写入。

## 注意事项

- 不保存原始整段问答，且不把助手生成的回答当作用户事实。
- 搜索的向量距离仅用于排序，不应把它展示成跨模型可比较的概率。
- pgvector 维度必须与所选 embedding 模型输出维度一致；当前表结构按项目配置的 768 维模型建立。
- `MemoryItem` 不包含 embedding；向量保存在 PostgreSQL 内部，不改公共数据契约。
