## 这个 PR 做了什么

修复 #72：扫描海报已经 OCR 入库，但问 `lecture6的language是什么` 时，检索把 `rag_paper.pdf` 第 14 页的无关片段交给回答模型，导致模型说找不到语言信息。

现在先识别问题所指的文档，再在该来源内检索。真实查询能返回 `HKU_TEC_x_Plug_and_Play.pdf` 第 1 页的 `Language / English`，真实云端模型连续问答返回 `lecture6的language是English [1]。`。同编号课程讲义可通过课程编号区分；无法唯一确定文件时直接要求用户指定文件，而不是凑满三条无关证据。

Closes #72

## 影响的模块

- [ ] `app/core`（公共结构 / 接口 / 配置）——没有修改
- [x] `app/agent`（A）——保留来源约束、传递和展示澄清信息
- [ ] `app/routing` `app/planning` `app/llm`（A）——没有修改实现
- [x] `app/ingestion` `app/retrieval` `app/memory`（B）——只改 retrieval；ingestion/memory 生产代码不变
- [ ] `app/verification` `app/ui` `app/evaluation`（C）——没有修改实现
- [x] `tests/` `docs/` `scripts/`

## 是否修改公共接口

- [x] 否：`Retriever.retrieve(query, top_k)`、`Evidence`、`AgentState`、数据库结构均保持不变，不需要数据迁移或重建已有索引。
- [ ] 是 → 改了哪些签名？影响谁？是否已通知 B / C？

补充说明：新增实现层可选属性 `HybridRetriever.last_clarification`，并通过已有 `ToolResult.metadata` 字典传递 `clarification`。这是一项新增诊断信息，未给公共 Protocol 增加必选方法或字段。A 用 `getattr(..., None)` 读取，其他 Retriever、Mock 和测试替身仍兼容。A/C 请核对该 metadata 的含义与展示。

## 根因

1. 实际库里同时有海报的 `LECTURE 6` 和课程讲义的 `COMP7608B · Lecture 6`，标题编号不是全库唯一。
2. 原逻辑只对完整 PDF 文件名做限定，其余问题走全库 BM25/Vector/RRF。短问题容易召回其他论文中的 Language 等词。
3. `lecture6` 与原文 `LECTURE 6` 的词形不一致；`language是什么` 的中英文连写不能用简单 Unicode 单词边界匹配。
4. “排在前三”不等于“来自正确文件”，RRF 是排名融合分数，不是答案置信度。本次没有用任意分数阈值来假装解决来源问题。

## 具体修改与工作流程

### B：先确定来源

新增 `app/retrieval/document_scope.py`：

- 用户写出已索引的完整文件名时优先使用该范围。
- 没有文件名时识别 `Lecture6`、`Lecture 6`、`Lecture 06` 等编号。
- 使用第一页开头 240 字符的标题区域建立候选，避免后页论文参考文献中的词语冒充课程标题。
- 有课程编号时进一步限定，例如 `COMP7608B`。
- 两份文档都叫 Lecture 6 时，检查问题中的日期、时间、地点、语言与原文结构化字段 `Date /`、`Time /`、`Venue /`、`Language /`（或冒号）。只有唯一候选包含全部所问字段，才选定该文件。
- 仍有歧义或找不到该标题时，给出文件名澄清提示，返回空证据。

### B：在限定范围内检索

`HybridRetriever` 使用来源解析结果调用原有 `_retrieve_documents`，SQL 的 `file_name = ANY(...)` 和 BM25 都限制在选定文件范围。

中文字段查询扩展为英文关键词，编号去掉前导零并统一空格；不改写原始 OCR 文本，不硬编码 English 为答案。Embedding 不可用时也只能在同一文件内用 BM25 降级。海报只有一条命中时返回一条，top-k 是上限，不是必须凑满的数量。

一般没有 Lecture 编号/文件名的问题仍沿用原有混合检索。现有总结与文件范围回归测试继续通过。

### A：最小必要的接线

- `_enrich_query` 给 Planner 子查询补回原问题的 Lecture 编号与课程编号，防止 `Lecture6 的 language` 被拆成只有 `language` 后重新失去来源约束。
- 每次检索后立即将 `last_clarification` 写入对应 `ToolResult.metadata`，避免下一次调用重置诊断属性后丢失信息。
- 本地文档存在来源歧义时跳过 Web Search。网页不能决定用户指的是哪份本地文件。
- `LLMAnswerGenerator` 优先返回澄清文本，不让模型替用户猜文件。

这里不是重写 A 的路由器或 LLM，也不是改回规则版 Memory。因为只改检索会导致 UI 仍显示泛泛的“无证据”，所以本 PR 包含这几处必要的接线，请 A 一并 review。

### C：如何使用与复验

普通回答继续从 `state.evidence` / citations 显示文件名、页码和原文。

有歧义时最终答案本身会提示候选文件，现有聊天界面即可显示。若 UI 要单独显示“请选择文档”，可读取本轮 retrieval 的 `tool_results[].metadata.clarification`；不要把它显示成“模型已回答”或“数据库连接失败”。

不依赖新的前端输入字段，不要求 C 先改 UI 才能合并。来源歧义并不是网页搜索失败，trace 会记录 `document_source_needs_clarification` 的跳过原因。

## 验证方式

```bash
uv run pytest tests/test_issue72_document_scope.py tests/test_document_coverage.py -q
uv run pytest -q
uv run ruff check .
git diff --check
uv run python scripts/check_issue72_retrieval.py
uv run python scripts/check_issue72_retrieval.py --real-answer
```

实际结果：

- Issue #72 新增 14 个用例；加上既有文档覆盖测试共 19 个通过。
- 全套 538 个测试通过。
- Ruff：`All checks passed!`；`git diff --check` 无输出。
- 真实 PostgreSQL + Ollama：指定海报文件、日期/地点、中英文 Language、课程编号、歧义提示均通过。
- 真实云端 LLM 两轮：海报日期/时间/地点正确引用 p.1；追问 Language 返回 English [1]，两轮错误列表均为空。

独立测试报告：[`docs/b-issue72-test-2026-11-21.md`](https://github.com/DaLinSang/academic_system/blob/codex/issue72-document-focus/docs/b-issue72-test-2026-11-21.md)。

`--real-answer` 会使用本机配置的真实模型并产生调用用量，拒绝 MockLLM，不输出 key。测试关闭长期记忆写入和网页搜索，不改写用户聊天记录；网页跳过逻辑由单元测试覆盖。这不是声称所有 Agent 功能都已端到端验证。

## 为什么调整两个旧 PDF 测试

`test_parse_pdf_skips_blank_pages` 和 `test_parse_pdf_returns_empty_list_when_all_pages_are_blank` 使用 `fake pdf content` 并模拟 PdfReader。电脑安装 OCR 工具后，这些伪文件触发了真正的 pdftoppm，因文件无效而失败。本 PR 仅把这两个“文本空白页”测试设为 `ocr_mode="off"`；没有屏蔽生产 OCR 失败，也没有删除 OCR 测试。

## 自检清单

- [x] 测试通过，且新增功能有对应测试
- [x] 没有提交 `.env` / API key / 真实 PDF / 用户聊天记录
- [x] 更新了本模块的 README
- [x] 关联对应 Issue：合并后由 `Closes #72` 自动关闭，当前不提前关闭
- [x] 已说明实验影响：本次改变检索来源选择，最终冻结版本需重跑相关检索及端到端评测；不把本次 smoke 当成 #49 的正式 50×4×3 数据

## 给 reviewer 的提示

B 是实现负责人；建议 A 核对 Planner/Orchestrator/AnswerGenerator 接线，C 核对文件页码、澄清显示和最终评测重跑。

重点看：

1. 明确文件名是否始终优先；同编号课程问题是否只返回课程资料。
2. `Language` 原文必须来自真实证据，不能把本 PR 理解为给海报硬编码答案。
3. 候选仍不唯一时是否真正在最终回答提示指定文件，且不触发网页替代。
4. 检索对象不保存跨请求的文档焦点，复用时不会将上一位用户的海报选择带入另一会话。这不等于新增文档权限隔离；共享文档库策略未改变。
5. 标题识别只覆盖第一页开头的 Lecture 编号及有限结构化字段。任意 OCR 错字、标题在后页、自由别名等无法保证自动解析；此时必须指定文件名。不能宣称通用文档理解已达到零错误。

合并后重启当前应用加载新代码；若海报已成功索引不必重复导入。仍须独立跟进 #70 Memory、#71 Web Search、#73 上传状态/UI 以及 #49 正式实验；本 PR 不关闭这些任务。
