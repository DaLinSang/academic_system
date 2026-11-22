# Changelog

本项目的所有重要变更都记录在这里。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本 2.0](https://semver.org/lang/zh-CN/)。

分类含义：`Added` 新增 / `Changed` 变更 / `Deprecated` 弃用 / `Removed` 移除 / `Fixed` 修复 / `Security` 安全。

> **维护规则**：PR 合并时就更新本文件，不要等发版时回忆。合并到 `main` 的改动先写到 `[Unreleased]` 下，发版时整体移到版本号下。

---

## [Unreleased]

### Added

- 测试 `tests/test_web_zero_result_71.py`（6 项）：零结果阻断、不误伤普通问答、结构化诊断不泄漏查询词（Issue #71）
- 测试 `tests/test_memory_json_contract_70.py`（13 项）：对象/数组兼容、可诊断解析失败、敏感过滤、已记录纠错（Issue #70）
- 测试 `tests/test_planned_source_64.py`（5 项）：Planner 子查询继承 PDF 来源、指代只用用户问题、trace 记录来源约束（Issue #64）
- 测试 `tests/test_web_search_regression_66.py`（4 项）：联网但未支撑答案时如实说明，区分工具未调用/失败/不相关（Issue #66）
- 测试 `tests/test_session_boundary_a32.py`（7 项）：SessionStore/RunLogger 边界、恢复后 turn_index 连续、会话/用户隔离、脱敏（Issue #32 A 部分）
- `MemoryStore.update()` and `delete()` now enforce `user_id` isolation; synchronized PostgreSQL, Mock, UI, tests, and module documentation. Workspace isolation remains deferred; PDF indexing is shared.

- `app/agent/history_context.py`：多轮历史格式化与字符预算（Issue #44）
- 测试 `tests/test_history_context.py`（9 项）、`tests/test_multi_turn_integration.py`（12 项）：历史预算、排序、三条 prompt 注入路径、规则路由防污染
- `app/agent/history_context.py`：多轮历史格式化与字符预算（Issue #44）
- 测试 `tests/test_history_context.py`（9 项）、`tests/test_multi_turn_integration.py`（12 项）：历史预算、排序、三条 prompt 注入路径、规则路由防污染
- `app/agent/orchestrator.py`：网页搜索接入编排器，本地证据不足且确实需要联网时按需调用（Issue #40/#41）
- `app/retrieval/web_search.py`：DuckDuckGo 网页搜索工具，结果转网页 Evidence（Issue #41）
- 测试 `tests/test_contract_web_evidence.py`（13 项）：文档/网页两种证据、locator 兼容、多轮字段向后兼容
- 测试 `tests/test_memory_preference_signals.py`（24 项）：Issue #31 的偏好识别、短信号误命中、去重与敏感过滤
- 测试 `tests/test_replan.py`（11 项）：校验失败触发重规划、新计划查询真的执行、预算上限、失败不毁回答
- 测试 `tests/test_retry_observability.py`（9 项）：重试恢复、不可重试错误、计数与延迟、trace 记录
- 测试 `tests/test_router_robustness.py`（21 项）：英文文档路由降级、大小写不敏感、
  泛化英文问句不误检索、`need_memory` 解析与语义
- **实验运行记录器**（`app/agent/run_logger.py`）
  - `RunLogger`：一次运行 = 一个目录，产出 `meta.json` + `samples.jsonl`
  - `meta.json` 记录五项复现上下文：commit / 模型 / 生成参数 / 数据版本 / 起止时间
  - `samples.jsonl` 一行一个完整 `AgentState`，直接可被 pandas / jq 读
  - 重名 run 自动加序号，避免两次实验混进同一份 JSONL
  - 异常中断也写 meta（含 `aborted` 与错误类型），留下「跑到哪断了」的证据
  - **写失败直接抛异常**，不静默丢数据
- **评测契约**（`docs/eval-contract.md`）
  - 定义入口、样本粒度、落盘结构、字段语义
  - RQ1–RQ4 每个指标的计算口径与字段来源
  - 实验纪律（同条件、重复三次、改评测必重跑、失败案例留档）
  - 五个已知数据坑（Mock token 是估算值、RRF 分值不可当概率、无证据时 claims 全 unsupported 等）
- 测试 `tests/test_run_logger.py`（12 项）
- **记忆写入决策**（`app/memory/manager.py`）
  - `LLMMemoryManager`：单次 LLM 调用判断「这段对话里有没有值得长期保存的信息」
  - 三道闸：关键词预筛（无信号不调模型）→ LLM 抽取 → 后处理过滤（敏感 / 重复 / 非法）
  - 敏感信息六类拦截：手机号、邮箱、身份证、银行卡、API key、学号等关键词
  - 去重：字符 bigram Jaccard ≥ 0.8，同时对比已有记忆与批内候选
  - `last_stats` 记录记忆漏斗（提议 / 采纳 / 各闸拦截数），供 RQ2 计算写入拒绝率
  - 记忆写入失败不影响回答，静默降级为空列表
  - **运行时默认启用**；`RuleBasedMemoryManager`（`policy.py`）保留但不启用，仅作消融基线
- **JSON 解析共享工具**（`app/core/json_utils.py`）
  - `extract_json_array` / `extract_json_object`：处理 json 围栏、前后夹带解释、字符串内含括号
- 模块文档 `app/memory/README.md`（顶部新增「运行时到底走哪个文件」对照表）
- 测试 `tests/test_memory_manager.py`（42 项）
- 测试 `tests/test_memory_wiring.py`（12 项）：钉死默认策略与其 `__module__`，防止重构时被改回去
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

- `app/agent/answer_generator.py`：web 已执行 count>0 时纠正“我无法联网”类错误表述，改为“已返回 N 条但无关”（Issue #83）
- `app/agent/orchestrator.py`：#83 纠正触发后不再追加重复的 web note（Issue #83）
- 测试 `tests/test_web_honesty_83.py`（5 项）：联网诚实性纠正与不误改边界（Issue #83）
- `app/memory/manager.py`：闸 1 信号补否定式长期偏好（“不要每次/以后别/don't always”等），一次性否定仍不误命中（Issue #82）
- 测试 `tests/test_memory_preference_signals.py`：新增 12 项否定偏好命中/一次性否定不误伤回归（Issue #82）
- `app/memory/manager.py`：闸 1 信号补否定式长期偏好（“不要每次/以后别/don't always”等），一次性否定仍不误命中（Issue #82）
- 测试 `tests/test_memory_preference_signals.py`：新增 12 项否定偏好命中/一次性否定不误伤回归（Issue #82）
- `app/agent/answer_generator.py`：联网已决策但零网页证据时不调 LLM 编造事实；网页证据 prompt 显示真实 URL 而非'第 0 页'（Issue #71）
- `app/retrieval/web_search.py`：新增 `last_diagnostics`（后端/失败类别/HTTP 状态，不含查询词）（Issue #71）
- `app/agent/orchestrator.py`：web/end trace 写入结构化诊断；避免与生成阶段失败说明重复（Issue #71）
- `app/memory/manager.py`：结构化输出统一为 `{"memories":[...]}`，兼容单项对象与旧顶层数组；解析失败记 `parse_detail`（Issue #70）
- `app/agent/orchestrator.py`：模型声称"已记录"但实际 stored=0 时，答案追加纠正说明（Issue #70）
- `app/agent/orchestrator.py`：新增 `_enrich_query` 统一给检索查询补文件名来源与历史指代；每个子查询写入 retrieval/query trace（Issue #64）
- `app/routing/router.py`：提示词补 need_web_search 判断规则（官网/最新/给链接 → true），示例 JSON 补字段（Issue #66）
- `app/retrieval/web_search.py`：Bing 的 /ck/a 跳转链接解码成真实 URL，用户拿到可点击来源（Issue #66）
- `app/agent/orchestrator.py`：本轮联网但无网页证据支撑时，答案末尾如实说明（Issue #66）
- `app/retrieval/web_search.py`：DuckDuckGo 不可达时自动降级到 Bing；默认超时 10s→5s；解析器同时支持两家 HTML 结构。修复部分网络下联网搜索一直 timeout 的问题
- `app/agent/README.md`：补 SessionStore 章节与 SessionStore/RunLogger 边界、turn_index 连续规则、不做 workspace 的决策；修正过期的'多轮未接入/web_search 未接入'描述（Issue #32 A 部分）
- `Orchestrator.run()` 新增可选 `history` 参数；Router/Planner Protocol 增加可选 `history`（Issue #44，向后兼容）
- `app/agent/answer_generator.py`：direct / grounded 两条路径注入历史对话，供模型消解『它/这个』指代
- `app/ui/streamlit_app.py`：把会话历史构造成 `ConversationTurn` 传给编排器，使多轮在前端可用
- `Orchestrator.run()` 新增可选 `history` 参数；Router/Planner Protocol 增加可选 `history`（Issue #44，向后兼容）
- `app/agent/answer_generator.py`：direct / grounded 两条路径注入历史对话，供模型消解『它/这个』指代
- `app/ui/streamlit_app.py`：把会话历史构造成 `ConversationTurn` 传给编排器，使多轮在前端可用
- **契约扩展**（Issue #39）：`Evidence` 新增 `url` / `title` / `is_web`，`RetrievalMethod` 新增 `WEB`，`AgentState` 新增 `history`
- `app/memory/manager.py`：预筛信号扩展「以后回答 / 每次回答 / 回答时请 / 从现在起」等稳定偏好表达（Issue #31）
- `app/memory/manager.py`：短信号（"我是"/"记住"）改为只在句首或标点后匹配，修复"告诉我是自己查的"误命中
- `app/memory/manager.py`：few-shot 明确"对未来回答方式的要求"属于 preference，即使句子形如指令
- `app/agent/orchestrator.py`：校验出现 unsupported claim 且任务有计划时触发一次重规划（Issue #47），预算硬上限 1 次
- `app/agent/orchestrator.py`：`llm` 类步骤由 PENDING 改为 SKIPPED，如实反映"有意跳过、由 AnswerGenerator 统一完成"
- `app/llm/client.py`：失败重试的等待时间计入 `Usage.latency_ms`，并暴露 `last_retries` / `last_retry_latency_ms`（Issue #48）
- `app/agent/orchestrator.py`：`_absorb_usage()` 把重试次数与额外耗时写进 trace
- `app/llm/client.py`：失败重试的等待时间计入 `Usage.latency_ms`，并暴露 `last_retries` / `last_retry_latency_ms`（Issue #48）
- `app/agent/orchestrator.py`：`_absorb_usage()` 把重试次数与额外耗时写进 trace
- `app/routing/router.py`：降级关键词加入英文（Issue #25）。此前只认中文，B 的英文 PDF 在无 key 时全部被误判为 direct
- `app/routing/router.py`：降级匹配改为 `casefold()`，英文大小写不敏感
- `app/routing/router.py`：系统提示词补充英文文档问题的判断规则，`need_memory` 语义改为「已有记忆是否有帮助」，并明确读取开关是 `enable_memory`
- `app/agent/orchestrator.py`：检索阶段现在把每次调用记进 `tool_results`（此前 `record_tool()` 从未被调用，该字段恒为空）
- `app/agent/orchestrator.py`：执行后回填 `PlanStep.status` 与 `result_summary`（`done` / `failed` + 命中条数或错误），未执行的 `llm` 步骤保持 `pending`
- `app/agent/orchestrator.py`：`current_step` 改为「已处理步骤数」，此前恒为 0
- `app/agent/orchestrator.py`：检索失败的错误统一走 `record_tool()`，不再单独手写 `state.errors`
- `app/agent/orchestrator.py`：记忆写回阶段现在会归集 MemoryManager 的 token 成本，并把记忆漏斗写入 `trace`
- `app/agent/factory.py`：`build_stage1_orchestrator()` 新增 `memory_strategy` 参数（`"llm"` 默认 / `"rule"`）；默认注入 `LLMMemoryManager`，规则版保留作 RQ2 消融基线
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
