# Member C Progress

更新日期：2026-11-24

当前开发分支：`feat/c-claim-context-rules`

基线提交：`origin/main@5564f58`（已包含 PR #104、#105）

## 2026-11-24 PDF 总结与 Claim 跟进

- Review PR #107 并请求修改：当前回答后补检索跳过 Markdown 事实列表、只处理
  前 5 句、缺少真正的文件范围检索，并且全文总结路径没有注入长期记忆。
- Claim Splitter 已排除 Markdown 标题、小节标签、来源区、代码块和表格分隔线，
  同时保留事实性列表项。
- 新增 `ClaimSpan` 字符偏移；A 的回答后检索与 C 的正文标记应统一复用同一套
  Claim 切分结果。
- 新增独立 `VerificationContext` 契约，按当前 Prompt > 最近会话 > 长期记忆
  排序；上下文来源明确不是学术引用，不修改冻结的 `Evidence` 公共结构。
- B 的 `delete_document()` 已接入右侧文档区；Real 模式删除前二次确认，同时清理
  数据库索引、页缓存和 `data/raw` 中的上传 PDF。
- 移除长期记忆面板中重复显示的用户 ID / 会话 ID，小幅清理演示界面，但不改动
  三栏布局、会话功能或现有交互。

## 2026-11-22 冻结状态

- PR #85 已合并：网页搜索已执行但结果无关时，不再错误声称“无法联网”
- PR #86 已合并：Lecture 文件名来源识别、多页综述和多文档比较证据覆盖
- PR #87 已合并：指令确认/系统元话语不再误判 unsupported；UI 默认 Top-K 调整为 5
- Issue #78、#79、#80、#81、#83 已随 PR 合并关闭
- `uv run pytest -q`、`uv run ruff check .` 在 `df2fb04` 全部通过
- DeepSeek-V3.2 真实 B4 q001 readiness run 已通过：路由正确、5 条证据、1 条
  supported claim、引用 precision/recall 均为 1.0、`errors=[]`
- readiness 结果位于本地 `runs/readiness-2026-11-22/20261122-165757-B4/`，
  仅用于确认正式实验条件，不作为最终 RQ1-RQ4 研究结果提交

## 2026-11-23 Review 与跟进

- PR #94 已合并并关闭 Issue #91：三栏聊天布局、会话列表、sticky 右栏、
  折叠证据面板和输入区上传入口已进入 main；A review 与 596 项测试通过
- PR #88 已合并：C 的任务边界、已完成能力、正式实验责任和剩余依赖已同步至
  项目进度文档
- PR #92 已合并：网页结果相关性过滤进入 main；Evidence、URL、title、
  diagnostics 与 Claim Verification 接口兼容
- PR #93 已合并：Chapter 来源限制进入 main；C 的来源、引用和校验展示接口无需修改
- PR #100 已合并：UI 已兼容显示网页候选全部无关、搜索后端失败、长期记忆新增、
  矛盾更新和更新失败；Issue #89、#97 的 C 展示部分均已完成
- PR #95、#96、#98、#102 已于 2026-11-23 合并：搜索 query 改写、Tavily
  失败回退、矛盾记忆 usage 归集及 Planner Chapter 约束均已进入 main
- C 跟进 Tavily 新增统计口径：RQ4 单列每查询 search credits 和总 credits，
  不与 LLM token/cost 混算，供正式 12-run 汇总比较

## 任务边界

依据项目规划和仓库分工，Member C 只负责：

1. Claim 拆分与证据校验
2. Streamlit Web 界面与验证结果展示
3. 小型标注数据集
4. B1-B4 评测脚本、结果整理和失败案例分析
5. Demo 中与 UI、Verifier、实验图表相关的部分

不修改 A 的编排逻辑、公共接口和模型调用层，也不实现 B 的检索、PDF 解析、
embedding、数据库或记忆存储。

## 已完成

### OCR 上传与记忆反馈 UI（Issue #65）

- 上传入口移动到聊天输入区附近，长对话滚动到底部即可快速导入或重试
- 上传状态明确区分保存、文字层检查、扫描页 OCR、embedding、索引和失败
- OCR 错误保留页码与工具诊断，不显示本机绝对路径，并给出安装/语言包提示
- 文档区区分已保存未索引、已索引、失败可重试与启动后状态未知
- Issue #73：文档区读取 PostgreSQL 持久化索引元数据；重导入失败与旧索引可用状态分开显示，并保留失败阶段、错误类型和诊断。公共 Retriever Protocol 未变。
- 回答来源区分未上传、已上传未索引、已索引未命中、文档、网页和 direct
- 记忆区显示当前 user/session、本轮写入状态和已保存数量；无记录与读取失败分开
- 运行信息从 trace 汇总可核对阶段、耗时、证据/claim/记忆数量，不展示内部思考
- 新增 OCR 预检测、错误脱敏、来源状态、阶段摘要测试及 Streamlit AppTest

Issue #49 已由负责人在 Issue 评论中改由 A 实现；C 已合入的数据集、runner、指标、
失败案例模板和 12-run 汇总能力继续保留。当前代码、模型 API、PostgreSQL/Ollama 与
50 条数据集均已通过 readiness 检查，A 可在冻结提交上执行正式 50×4×3 实验；C
负责 review 生成的 12 个 run、指标表和失败案例，不重复承担正式运行任务。

### Web 来源与会话 UI（Issue #42 / #46）

- 文档引用继续显示文件名与页码，网页引用显示标题/域名并可点击
- Claim Verification 的 supporting evidence 可定位到网页 URL
- 运行信息显示网页搜索未触发、成功条数、无结果或失败原因
- 来源状态区分上传文档、网页、direct、检索无证据和检索失败
- 侧栏支持 user_id 与会话列表，并可创建、切换、重命名和删除会话
- 页面刷新或服务重启后通过 SessionStore.restore 恢复完整 AgentState
- “清空当前会话”仅清除当前 JSONL，不修改长期记忆或 PDF
- 已补纯 presenter、SessionStore 与多轮集成测试

### Web / 多轮评测与数据集扩展（Issue #43 / #49）

- 标注集从 12 条扩展到 50 条，覆盖中英文、三类 route、Web、无证据和三组两轮记忆
- 新增可选 `gold_need_web`，旧数据默认 false 并保持向后兼容
- RQ1 新增 Web 搜索触发准确率与不必要联网率
- RQ2 新增跨轮记忆命中率
- RQ3 新增网页引用有效率
- RQ4 使用 pipeline trace 墙钟，避免组件 latency 与总 latency 重复累计
- batch runner 支持 `repeats=3`，自动生成 4x3 共 12 个 run
- 新增 `experiment_summary.json`，按模式输出 mean、sample std 与 n
- Mock 50 条 4x3 已验证 12 个目录、metrics 与五项 meta 完整
- Real B4 q001 已用 pgvector + Ollama embedding + 本地聊天模型跑通；本地 1B
  输出仅作为链路 smoke，不冒充团队正式模型实验结果

### Claim Verification V1

- 回答拆分为独立 claim
- `supported`、`partially_supported`、`unsupported` 三分类
- 置信度、真实 Evidence `chunk_id` 和说明
- 空证据及内部异常降级
- 固定 claim 测试集、单元测试和 B4 集成测试
- 模块 README
- 已通过 PR #9 合入 `main`

### Streamlit UI V1

- 四区页面基础结构：设置、聊天、文档、记忆
- Mock / Real 和 B1-B4 模式
- PDF 上传与 Retriever 接口导入
- 回答、页码引用和证据原文
- Claim 绿色、黄色、红色状态、置信度和证据页码
- 记忆查看、编辑和删除
- 运行 token、成本、延迟及错误展示
- UI 纯展示逻辑测试和 Mock 浏览器验收
- 已通过 PR #23 合入 `main`

当前状态：Claim Verification、Streamlit UI、50 条标注集、B1-B4 批量评测、
RQ1-RQ4 指标和失败案例框架均已进入 `main`。C 的功能开发已完成并进入冻结后的
实验 review 阶段。

### Annotated Dataset V1

- 建立冻结的 JSON Schema：`app/evaluation/datasets/annotated_v1.schema.json`
- 建立首批 12 条 JSONL：10 条路由/检索样本和 1 组两轮记忆样本
- 使用 `gold_sources` 同时记录文件名和页码，避免多文档页码冲突
- 保留 `gold_pages` 兼容 `docs/eval-contract.md`
- 标注覆盖 direct、document_retrieval、complex_task、英文/中文、单文档、
  多文档、BM25、RRF、Memory read/write
- 对真实 PDF 页码完成文本提取与页面渲染复核
- 增加数据完整性、路由语义、PDF 页码范围和跨会话顺序测试

### B1-B4 Batch Runner V1

- 支持从冻结 JSONL 读取样本并校验必需字段与重复 ID
- 支持 `--modes`、`--top-k`、`--limit`、`--real` 和自定义 runs 目录
- 每个模式创建独立 Orchestrator 和 RunLogger 目录，避免基线间状态污染
- 同一模式内保持数据集顺序，使两轮 RQ2 样本共享 MemoryStore
- 将样本 ID 顺序和 Mock/Real runtime 写入 run metadata
- 已用 Mock B1/B4 完成端到端 smoke run
- 明确 MockRouter 结果不能用于评价 A 的英文 Router 修复

### RQ1-RQ4 Metrics V1

- 新增 `app/evaluation/metrics.py`，读取 A 的 `meta.json` 与 `samples.jsonl`
- 使用 `_sample_index` 和 `meta.extra.sample_ids` 对齐冻结数据集，防止筛选运行后错位
- RQ1 输出 route accuracy、不必要检索率、漏检率和固定 3×3 混淆矩阵
- RQ2 输出 memory hit rate、memory write rejection rate 和逐样本跨会话结果
- RQ3 输出 claim support rate、citation precision，并使用 `gold_sources` 计算
  文件名+页码级 citation recall
- RQ4 输出单查询 token、成本、延迟及错误恢复成功率
- 零分母统一输出 JSON `null`，避免把“不可计算”误报成 0
- 无 Evidence 样本不进入 claim 支持率分母，符合评测契约的数据坑说明
- 新增合成单元测试，覆盖四个 RQ、混淆矩阵、文件页引用和零分母行为

## 2026-11-17 合并后复核

PR #24 合入后，C 已重新核对 `docs/b-demo.md` 第 6 节接口：

- UI 通过 `AgentState.evidence` / `Citation` 展示真实文件名、页码和证据文本。
- `LexicalVerifier.supporting_evidence` 只使用传入 Evidence 中存在的真实 `chunk_id`。
- Claim 展示层使用 `evidence_by_chunk_id()` 解析定位，并过滤不存在的 ID。
- 记忆界面先调用 `MemoryStore.list_all(user_id)`，再使用返回条目的 ID 调用
  `update()` / `delete()`；没有直接访问数据库。
- 关闭长期记忆通过 Orchestrator 的 `enable_memory=False`，符合接口约定。
- PDF 导入通过 `Retriever.index_document()`，没有调用 B 的数据库内部实现。

结论：A/B/C 的现有接口可以支持 C 开始真实联调和评测，不需要修改冻结的公共接口。
Router 英文降级规则仍归 A；英文切分、字符偏移、页码和 BM25 全量重载归 B。
C 只记录这些依赖的测试结果，不接管其实现。

## 2026-11-18 Demo Review 跟进

- 已处理 B 在 PR #35 提出的 T06：claim splitter 不再在中英文分号处截断，
  百分比与 RAG/BART 比较上下文保持在同一 claim。
- 已处理 T07 / Issue #34 的 C 侧问题：资料不足拒答不进入事实 claim 校验，
  UI 只展示回答实际使用的引用，不展示全部 Top-K 候选页码。
- 已补充 Issue #31 的 C 侧展示：逐轮区分记忆保存成功、无稳定信息、重复/敏感
  过滤、候选生成失败和写入失败；不把模型口头承诺当成已落库。
- UI 明示当前 user_id、固定会话 ID 和“共享文档库”，避免误以为已经实现用户或
  工作区隔离。
- Issue #31 的偏好识别与写入策略仍由 A 负责；真实 PostgreSQL/Ollama 存取由 B
  验证。Issue #32 的持久会话与工作区隔离涉及尚未确认的公共契约，C 不在 UI 内
  伪造隔离能力，待团队确定 ConversationStore/workspace_id 方案后继续。
- 当前 C 分支与 PR #35 通过 merge-tree 检查，无文件冲突。

A 的英文 Router 与 `need_memory` 修复已通过 PR #27 合入 `main`。后续 C 的 RQ1
评测应使用该版本作为基线，不再把已修复的英文降级行为列为当前代码阻塞。

## 组员模块现况与 C 的可用接口

以下内容只记录依赖是否可用；C 不修改其内部实现。

| 模块 | 负责人 | GitHub 最新状态 | C 的使用方式 |
|---|---|---|---|
| Router / Planner / AnswerGenerator | A | 已合入 `main` | B1-B4 直接运行 Orchestrator |
| 工具调用追踪与步骤状态 | A | 已合入 `main`（PR #20） | 失败案例读取 `tool_results` 与 `plan` |
| LLM MemoryManager | A | 已合入 `main`（PR #22） | RQ2 读取 memory trace 与 usage |
| RunLogger 与评测契约 | A | 已合入 `main`（PR #19） | C 读取 `runs/*/samples.jsonl` 计算指标 |
| PDF 解析与页码保留 | B | PR #24 已合入 `main` | 标注和引用使用真实页码与 chunk_id |
| BM25 / Vector / RRF 检索 | B | PR #24 已合入 `main` | RQ1、RQ3 使用 evidence 与 retrieval_method |
| PostgreSQL MemoryStore | B | 已合入 `main`（PR #17） | UI 和 RQ2 只通过 MemoryStore 接口访问 |
| Claim Verification | C | 已合入 `main`（PR #9） | RQ3 使用 claims 与 supporting_evidence |
| Streamlit UI V1 | C | PR #23 已合入 `main` | 展示回答、引用、claim、文档和记忆 |

结论：A/B 已提供 C 开展真实联调和评测所需的数据接口。C 不需要继续等待，
也不需要修改 `app/core/`、`app/agent/`、`app/retrieval/` 或 `app/memory/`。

### 当前复现条件

代码层面的 PDF、Hybrid Retrieval、接口契约、Verifier 与 UI 测试均已通过。
三份约定 Demo PDF 已进入 `data/raw/` 并用于首批真实页码标注。真实 B2/B4
联调仍需要在执行机器上补齐：

- PostgreSQL/pgvector；
- Ollama 的 `nomic-embed-text`；
- 若要评价答案质量和英文 Router，需要可用的聊天模型 API。

缺少聊天 API 时，真实 Retrieval 仍可单独用 B2 验证；固定 Mock Answer 与 Evidence
不一致而被 Verifier 判为 unsupported 属于预期行为。

## 已确认的评测约定

根据 `docs/eval-contract.md`，C 采用以下约定：

- 标注数据集使用 JSONL。
- 每行至少包含 `id`、`question`、`user_id`、`gold_route`、`gold_pages`、
  `gold_claims`；记忆样本补充轮次与相关记忆标注。
- B1-B4 使用相同问题、模型、PDF、温度和 Top-K。
- 每个模式单独生成一个 `runs/<run_id>/` 目录。
- 原始结果由 A 的 `RunLogger` 写入 `meta.json` 和 `samples.jsonl`。
- C 自己实现批量运行入口和指标计算，不要求 A 重复实现。
- Streamlit 后续直接读取 `runs/` 下的 run 目录展示实验结果。
- 正式实验重复三次，结果报告均值与标准差。
- 失败案例写入 `docs/failure-cases.md`，保留原始问题、预测、证据和错误类型。

### 指标责任

| RQ | C 负责计算的核心指标 | 主要输入 |
|---|---|---|
| RQ1 路由 | accuracy、不必要检索率、漏检率、3×3 混淆矩阵 | `route` + `gold_route` |
| RQ2 记忆 | 记忆命中率、写入拒绝率、跨会话完成度 | `memory_context` + memory trace |
| RQ3 验证 | claim 支持率、引用精确率、引用召回率 | `claims`、citation audit、`gold_pages` |
| RQ4 效率 | 单查询 token、成本、搜索 credits、延迟、错误恢复成功率 | `usage`、Web trace、`errors`、`final_answer` |

## 下一步执行顺序

1. 用最新 main 复查 #89、#90 的真实联网与 Chapter 来源验收，并关闭已完成 Issue。
2. 团队确认最终冻结提交及演示机器的 Tavily、模型、PostgreSQL/Ollama 环境。
3. A 在最终冻结提交上运行 50 条 × B1-B4 × 3 次正式实验。
4. C 检查 12 个 run 的 `meta.json`、汇总指标和失败案例，再冻结报告表格。

当前联网、检索、记忆与 Chapter 约束相关 PR 均已进入 main。C 新增的 search credits
RQ4 统计合并后，可在团队确认最终冻结提交和真实运行环境后开始 50×4×3 实验。
