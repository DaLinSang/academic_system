## 这个 PR 做了什么

用户上传 `COMP7608B_Lec04.pdf` 后问“COMP7608B Lecture 04 里面都讲了什么”，旧版本因为文件名是 Lec04、第一页内部写的是 Lecture 1 而误报找不到文档。即使指定文件，问“都讲了什么/整理复习提纲/比较两份 PDF”，旧检索也可能只给模型两三页，导致回答过短或误说“证据不足”。

本 PR 修复可见文件名与内部标题冲突，识别口语化学习问题，从同一 PDF 的不同页面选代表性原文；两份文献比较时，模型可见的前六条证据会包含两边。回答模块按引用证据核对紧邻的页码，避免把 `[6]` 误读为“第 6 页”。

Closes #78
Closes #79

## 影响的模块

- [ ] `app/core`（公共结构 / 接口 / 配置）——未修改
- [x] `app/agent`（A）——学习任务证据预算、比较生成提示、紧邻引用页码核对；请 A review
- [ ] `app/routing` `app/planning` `app/llm`（A）——未修改
- [x] `app/ingestion` `app/retrieval` `app/memory`（B）——只修改 Retrieval
- [ ] `app/verification` `app/ui` `app/evaluation`（C）——未修改
- [x] `tests/` `docs/` `scripts/`——新增测试与文档，没有改脚本

## 是否修改公共接口

- [x] 否：`Retriever.retrieve(query, top_k)`、`Evidence`、`AgentState`、数据库 schema、模型提供方配置均保持原样。不需要数据迁移、重新生成 Embedding 或重新导入已有 PDF。
- [ ] 是 → 改了哪些签名？影响谁？是否已通知 B / C？

## 根因与具体修改

### #78：用户看到的文件名应能识别

`document_scope` 识别 `Lec04`、`Lecture_04`、`Lecture 04` 和 `comp7608_04` 等写法。查询带课程编号、文件名中编号唯一时，使用该 PDF；不让正文内部“Lecture 1”否定文件名“Lec04”。已有完整文件名仍优先。没有课程编号且多份材料同为 Lecture 6 时，继续按结构化字段或课程线索区分；无法确定就列候选文件，守住 #72 的正确来源行为。

### #79：多页综述和多文档比较

`study_intent.is_study_request()` 将“都讲了什么、有哪些内容、总结、复习提纲、比较”及对应英文问法识别为需要广覆盖的学习任务；`Self-RAG 是什么` 等精确问题继续沿用普通检索。

限定来源后，检索在每份文档的不同页面区间挑选有实质内容的 chunk，并尽量覆盖摘要、方法、实验、讨论/结尾；论文有可识别的 References 标题时避开后续书目页。Embedding 可用时参与候选评分，离线时仍能基于已载入文本选页并标记降级。

两份 PDF 比较时各得 3 条证据，文件交错排列。`Orchestrator` 对学习任务至少申请 6 条证据，Planner 多次检索后仍优先把不同来源和不同页放入生成模型实际可见的前六条。网页工具若随后加入证据，也参与该排序。模型使用原文 `Evidence` 生成，页码仍取自索引，C 的来源引用/Claim Verification 可照常工作。

生成提示要求按主题总结、比较双方观点、整理复习材料；如果某概念只有一方材料讨论，不得虚构双方共同点。模型写“第 6 页 [6]”而 `[6]` 来自第 9 页时，生成模块会校正紧邻引用的页码，记录 `page_citation_correction`；多个紧邻引用对应不同页时列出各页。没有有效引用的独立页码不会自动更改。

## 实际验证

```bash
uv run pytest tests/test_study_retrieval.py tests/test_issue72_document_scope.py tests/test_document_coverage.py -q
uv run pytest -q
uv run ruff check .
git diff --check
```

- 全套 **581 个测试通过**；Ruff 输出 `All checks passed!`；差异空白检查通过。
- 找到真实 `COMP7608B_Lec04.pdf`，直接解析得到 79 页、79 个 chunk。临时内存索引在 Embedding 关闭时，`COMP7608B Lecture 04 里面都讲了什么` 返回同一文件的 p.2、21、28、52、64、71；`comp7608_04` 问法也定位同一文件。此 PDF 当前尚未导入本地 PostgreSQL，**没有声称 UI 中该文件已索引**。
- 对已索引的 `rag_paper.pdf`，真实 PostgreSQL + Ollama 给出 p.1、3、4、5、7、9 的综述证据，没有拿后部书目页填满预算；对比现有讲义时两份材料各 3 条，顺序交错。
- 真实云端模型一次论文总结返回研究目的、方法、结果；一次两文档比较返回双方观点；两轮 `state.errors=[]`。另一次启用 Claim Verification 的论文总结得到 18 条 claim、0 unsupported、0 错误。实际模型曾把 `[6]` 写成第 6 页，经新增页码核对和单元测试覆盖后，紧邻引用的页码已能纠正。
- `lecture6的language是什么` 仍只命中 OCR 海报 p.1，#72 的来源限制没有回归。

完整复现输入、观测与边界在 [B 学习型检索验证记录](https://github.com/DaLinSang/academic_system/blob/codex/issue78-79-study-overview/docs/b-study-overview-validation-2026-11-22.md)。

## UI / A / C 如何使用

合并并更新本地代码后，在 Real 模式确认 PDF 的状态是“已建立索引”，再问：

1. `COMP7608B Lecture 04 里面都讲了什么？`（需要先上传 `COMP7608B_Lec04.pdf`）
2. `请总结 rag_paper.pdf 的研究目的、方法与结论，并给出引用。`
3. `请比较 rag_paper.pdf 和 comp7608_lecture_rag.pdf 对检索与智能体的描述。`
4. `根据 rag_paper.pdf 整理一份复习提纲。`
5. `只根据 HKU_TEC_x_Plug_and_Play.pdf 第1页，Language 字段是什么？`（#72 回归）

展开来源引用，检查文件名、实际页码和正文是否相符。UI 默认 Top-K 当前为 3，但学习问题由 Agent 自动提高到至少 6；C 的 #81 可以再决定是否调整滑块默认值和说明。没有明确文件名且共享库里有多份 PDF 时，系统会要求指定文件；这并非索引失败。

A 重点 review `app/agent/orchestrator.py` 与 `answer_generator.py` 中的证据顺序、Web 追加证据及页码核对；C 重点核对来源引用显示、Claim Verification 口径、#49 的最终评测重跑。B 重点核对 `document_scope.py` 和 `hybrid_retriever.py` 的来源与页区间选择。

## 自检清单

- [x] 测试通过，且新增功能有针对用户真实失败形态的回归测试
- [x] 未提交 `.env`、API key、真实课程 PDF、用户聊天或数据库记录
- [x] 更新了 Retrieval 和 Agent 模块 README，另附独立测试记录
- [x] `Closes #78`、`Closes #79`：合并后自动关闭，PR 创建时仍保持 Issue 开放
- [x] 已说明实验影响：改变了证据选择和生成提示。#49 的 RQ1–RQ4 正式实验须在冻结版本统一重跑；本次 smoke 不能代替正式 50×4×3 数据

## 给 reviewer 的提示与边界

本次是**有来源的代表性多页总结**。一份 79 页讲义在六条证据预算下仍不可能逐段穷尽；需要精确的章节细节应继续指定章节或页码。对两份材料主题不完全相同的比较，模型仍可能得出过度概括的共同点，应看 Claim Verification 和引用原文。OCR 错字、未索引文件、隐藏的“当前文档”上下文均不能由检索层自动推断。合并后应请 C 在界面上跑上述问题并记录 trace，确认真实部署的 PDF 索引状态。
