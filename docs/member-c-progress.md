# Member C Progress

更新日期：2026-11-16

当前开发分支：`feat/c-streamlit-ui-v1`

基线提交：`origin/main@ad31c87`

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

当前状态：代码已在本地分支完成并同步到最新 `origin/main`，尚未推送和创建 PR。

## 组员模块现况与 C 的可用接口

以下内容只记录依赖是否可用；C 不修改其内部实现。

| 模块 | 负责人 | GitHub 最新状态 | C 的使用方式 |
|---|---|---|---|
| Router / Planner / AnswerGenerator | A | 已合入 `main` | B1-B4 直接运行 Orchestrator |
| 工具调用追踪与步骤状态 | A | 已合入 `main`（PR #20） | 失败案例读取 `tool_results` 与 `plan` |
| LLM MemoryManager | A | 已合入 `main`（PR #22） | RQ2 读取 memory trace 与 usage |
| RunLogger 与评测契约 | A | 已合入 `main`（PR #19） | C 读取 `runs/*/samples.jsonl` 计算指标 |
| PDF 解析与页码保留 | B | 已合入 `main` | 标注和引用使用真实页码 |
| BM25 / Vector / RRF 检索 | B | 已合入 `main` | RQ1、RQ3 使用 evidence 与 retrieval_method |
| PostgreSQL MemoryStore | B | 已合入 `main`（PR #17） | UI 和 RQ2 只通过 MemoryStore 接口访问 |
| Claim Verification | C | 已合入 `main`（PR #9） | RQ3 使用 claims 与 supporting_evidence |
| Streamlit UI V1 | C | 本地完成，待 PR | 展示回答、引用、claim、文档和记忆 |

结论：A/B 已提供 C 开展真实联调和评测所需的主要数据接口。C 不需要继续等待，
也不需要修改 `app/core/`、`app/agent/`、`app/retrieval/` 或 `app/memory/`。

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

1. 推送 `feat/c-streamlit-ui-v1` 并提交 UI V1 PR。
2. 新建独立评测分支，定义 JSONL schema 并先提交 5-10 条可审查样例。
3. 编写 B1-B4 批量运行脚本，调用现有 `RunLogger`，不自行重复落盘逻辑。
4. 编写 RQ1-RQ4 指标计算和结果表导出。
5. 在 Real 环境完成一次小规模 smoke run，再扩展到完整标注集并重复三次。
6. 将 runs 汇总接入 Streamlit 实验结果页。
7. 准备固定 Demo 问题、unsupported claim 示例和失败案例文档。

当前无代码接口阻塞。Real 实验仍要求组员共同提供可用的 API key、PostgreSQL、
Ollama embedding 和统一测试 PDF，但这些属于运行环境，不属于 C 的实现任务。
