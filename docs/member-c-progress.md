# Member C Progress

更新日期：2026-11-17

当前开发分支：`feat/c-evaluation-data-v1`

基线提交：`origin/main@546f75e`（已包含 C 进度 PR #26、A Router PR #27 与三份 Demo PDF）

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

当前状态：Claim Verification V1 与 Streamlit UI V1 均已进入 `main`。C 的下一阶段
转为标注数据集、B1-B4 批量评测、指标计算和实验结果展示。

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
| RQ4 效率 | 单查询 token、成本、延迟、错误恢复成功率 | `usage`、`errors`、`final_answer` |

## 下一步执行顺序

1. 请团队审核 `annotated_v1` 的 12 条问题、gold route、gold sources 和 claims。
2. 请团队审核 B1-B4 batch runner 的参数和 run metadata。
3. 团队审核 `metrics.json` 字段和零分母使用 `null` 的约定。
4. 继续补充 C 侧接口回归测试：Evidence 页码/文本展示、无效 supporting ID 过滤、
   当前用户 Memory ID 的更新与删除。
5. 按 `docs/b-demo.md` 在 Real B2 完成小规模 smoke run，再运行 B3/B4。
6. 将 `runs/` 汇总接入 Streamlit 实验结果页。
7. 扩展完整标注集，正式实验重复三次并报告均值 ± 标准差。
8. 准备固定 Demo 问题、unsupported claim 示例和 `docs/failure-cases.md`。

当前无代码接口阻塞。首批数据集、批量运行器和 RQ1-RQ4 指标计算已完成；Real B2 smoke run 取决于
PostgreSQL 和 Ollama embedding，真实答案质量及 B3/B4 完整评测还需要聊天模型配置。
