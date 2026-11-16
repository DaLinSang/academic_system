# Changelog

本项目的所有重要变更都记录在这里。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本 2.0](https://semver.org/lang/zh-CN/)。

分类含义：`Added` 新增 / `Changed` 变更 / `Deprecated` 弃用 / `Removed` 移除 / `Fixed` 修复 / `Security` 安全。

> **维护规则**：PR 合并时就更新本文件，不要等发版时回忆。合并到 `main` 的改动先写到 `[Unreleased]` 下，发版时整体移到版本号下。

---

## [Unreleased]

### Added

- 测试 `tests/test_tool_tracking.py`（13 项）：工具调用记录、步骤状态回填、失败不中断后续步骤、无计划时退化用原问题
- **LLM 路由器**（`app/routing/router.py`）
  - `LLMRouter`：单次 LLM 调用做三分类（direct / document_retrieval / complex_task），temperature 固定为 0 保证可复现
  - `RuleBasedRouter`：纯关键词基线，用于对照「用模型做路由是否值得」
  - JSON 解析容错：支持 json 围栏、前后夹带解释、括号内含花括号的字符串
  - 三类归一化：消除模型自相矛盾输出（direct 不检索、document_retrieval 必检索、complex_task 必规划）
  - 两级降级：JSON 解析失败或模型调用失败均回退关键词启发式，绝不抛异常
  - `last_usage` 暴露本次路由的 token 与成本，由 Orchestrator 归集
- 模块文档 `app/routing/README.md`
- 测试 `tests/test_router.py`（27 项）
- **基于证据的回答生成**（`app/agent/answer_generator.py`）
  - `LLMAnswerGenerator`：把编号后的证据拼进提示词，要求每条事实标注 `[n]` 引用
  - 三条路径：direct 用模型自身知识 / 有证据走 grounded 生成 / 需要证据但无证据时明确拒答
  - 无证据时**不调用模型**，从源头杜绝幻觉
  - 引用审计：越界编号保留原文并记入 `trace`，作为 citation precision 的失败样本
  - 降级：模型不可用时返回带页码的原文摘录，保证 Demo 不出现空白页
- 模块文档 `app/agent/README.md`
- 测试 `tests/test_answer_generator.py`（18 项）
- **任务规划器**（`app/planning/planner.py`）
  - `LLMPlanner`：单次 LLM 调用把复杂请求拆成 2–5 步，temperature 固定为 0
  - 仅 `complex_task` 触发拆解，其余两类返回空列表且不消耗模型调用
  - 步骤编号重排为 1..n，并按原编号映射翻译 `depends_on`，避免依赖关系失效
  - 检索步骤缺 `query` 时补原问题，防止该步骤白拆
  - 降级：解析失败 / 调用失败 / 空数组一律返回最小两步计划，不返回空
- 模块文档 `app/planning/README.md`
- 测试 `tests/test_planner.py`（21 项）
- **阶段 1 真实装配**（`app/agent/factory.py`）
  - `build_stage1_orchestrator()`：LLM 路由 + LLM 规划 + 混合检索(B) + 词法校验(C) + LLM 生成
  - 记忆仍接 Mock，等 B 交付 `MemoryStore` 后只改这一行
  - 延迟导入真实模块，保证 `build_mock_orchestrator()` 不依赖 psycopg / httpx
- `app/demo.py` 新增 `--real`，与 Mock 模式共用同一套 Orchestrator
- `scripts/index_pdfs.py`：批量把 PDF 索引进 pgvector，支持 `--dir` / `--limit` / `--list`
- 测试 `tests/test_factory.py`（11 项）：装配正确性、构造不连库、检索失败降级、用量归集
- 协作规范 `CONTRIBUTING.md`：分支、提交、版本、PR、Issue、代码规范与实验复现规则
- 编辑一致性配置 `.editorconfig`，统一三人 IDE 的缩进与换行

### Changed

- `app/agent/orchestrator.py`：检索阶段现在把每次调用记进 `tool_results`（此前 `record_tool()` 从未被调用，该字段恒为空）
- `app/agent/orchestrator.py`：执行后回填 `PlanStep.status` 与 `result_summary`（`done` / `failed` + 命中条数或错误），未执行的 `llm` 步骤保持 `pending`
- `app/agent/orchestrator.py`：`current_step` 改为「已处理步骤数」，此前恒为 0
- `app/agent/orchestrator.py`：检索失败的错误统一走 `record_tool()`，不再单独手写 `state.errors`
- `app/agent/orchestrator.py`：`_absorb_usage()` 现在同时回收 Router 与 Planner 内部产生的 token 与成本，避免 RQ4 统计偏低
- 路由启发式关键词收紧：移除「什么是 / 定义 / 提到」等泛化词，避免把概念性问题误判为需要检索，抬高 RQ1 的不必要检索率
- `app/core/interfaces.py`、`app/core/mocks.py`：记忆存储方案表述由「SQLite/PostgreSQL」明确为 PostgreSQL + pgvector
- `CONTRIBUTING.md`：PR 大小限制明确为「实现代码 ≤400 行，测试与文档不计入」

### Fixed

- `.gitignore` 增加 `.pytest_tmp/`

---

## [0.1.0] - 2026-11-03

阶段 0：骨架与公共接口冻结。目标是让 B 和 C 不必等彼此，可以基于 Mock 并行开发。

### Added

- **公共数据结构**（`app/core/types.py`）
  - 7 个核心结构：`DocumentChunk` `Evidence` `RouteDecision` `MemoryItem` `ClaimCheck` `ToolResult` `AgentState`
  - 5 个辅助结构：`MemoryCandidate` `Citation` `PlanStep` `Usage` `TraceEvent` `LLMResponse`
  - 5 个枚举，统一使用 `StrEnum`
  - `DocumentChunk` 强制 `page_number >= 1` 且文本非空，防止页码在解析阶段丢失
- **模块接口**（`app/core/interfaces.py`）
  - 8 个 `Protocol`：`Retriever` `MemoryStore` `MemoryManager` `Verifier` `Router` `Planner` `LLM` `AnswerGenerator`
  - 明确每个接口的失败契约（无命中返空列表 / 服务不可用抛类型化异常 / 空证据全判 unsupported）
- **统一配置层**（`app/core/config.py`）
  - 四组配置：LLM、Embedding、Database、Runtime
  - `LLMSettings.resolve()` 支持 DeepSeek 与港大 API 切换，换模型只改 `.env`
- **共享异常**（`app/core/errors.py`）
  - 6 个类型化异常，作为跨模块边界的统一契约
- **Mock 全链路**（`app/core/mocks.py`）
  - 8 个 Mock 实现，签名与真实实现一致
  - `MockVerifier` 实现 claim 拆分过滤，剔除引导语、结语与纯引用标记
- **Agent 编排**（`app/agent/orchestrator.py`）
  - 8 阶段流水线：读记忆 → 路由 → 规划 → 检索 → 生成 → 校验 → 组装 → 写记忆
  - 每阶段独立降级，单个工具失败不影响整体出答案
  - `MODE_PRESETS` 定义 B1–B4 四种消融配置
- **装配工厂**（`app/agent/factory.py`）
  - Mock 与真实实现可互换，调用方零改动
- **LLM 统一调用层**（`app/llm/client.py`）
  - OpenAI 兼容封装，DeepSeek 与港大 API 共用
  - 指数退避重试，仅对超时 / 限流 / 5xx 重试
  - token 与成本记账，作为 RQ4 数据来源
  - 未配置 key 时自动退回 `MockLLM`，保证干净环境可运行
- **演示入口**（`app/demo.py`）
  - `uv run python -m app.demo [-m B1|B2|B3|B4]`，打印 8 阶段状态与自检结果
- **环境自检**（`scripts/check_env.py`）
  - 检查 LLM 连通性、Ollama embedding 维度、pgvector 扩展
- **工程化**
  - `pyproject.toml`：Python 3.11 + uv，ruff 与 pytest 配置
  - `docker-compose.yml`：独立 pgvector 容器，端口 5434，避开本机其它项目
  - `.env.example`、`.gitignore`、PR 模板、Issue 模板、模块 README 模板
- **测试**（`tests/test_contracts.py`）
  - 24 项契约测试：接口一致性、数据结构校验、四模式端到端、B1/B2 隔离、错误降级

### Fixed

- B1 与 B2 配置完全相同导致两组基线实验跑同一系统：`AgentState` 新增 `enable_retrieval` 开关
- `MockVerifier` 将「根据检索到的文档：」等元语句误判为 claim，虚高 unsupported 率
- `str, Enum` 在 Python 3.11 下 `str()` 输出 `ClaimStatus.UNSUPPORTED`：统一改为 `StrEnum`
- 无检索证据时重复输出「无证据支持」警告：`_compose_final` 增加空证据短路

### Security

- `.env`、API key、真实 PDF 数据均已加入 `.gitignore`
- 未配置 key 时不会尝试发起真实网络请求

---

## 版本里程碑对照

| 版本 | 阶段 | 判定标准 | 状态 |
|---|---|---|---|
| v0.1.0 | 阶段 0：骨架与接口冻结 | Mock 全链路跑通，24 项测试绿 | ✅ 已完成 |
| v0.2.0 | 阶段 1：三个核心模块可用 | LLM 层、检索、校验能独立跑 | ⏳ 进行中 |
| v0.3.0 | 阶段 2：Agent 完整流程 | 记忆 + 多步任务 + 错误恢复可用 | 计划中 |
| v0.4.0 | 阶段 3：界面与评测 | Streamlit 可演示，B1–B4 能跑 | 计划中 |
| v0.9.0 | 功能冻结 | 停止加功能，只修 bug 与跑实验 | 计划中 |
| v1.0.0 | 最终交付 | 干净环境可复现，报告与代码一致 | 计划中 |
