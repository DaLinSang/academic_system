# B4 真实链路 Demo 测试记录（2026-11-18）

本文记录在 Streamlit 界面手工执行的端到端测试，供 A、B、C 复现、修复和撰写最终报告。这里的 **Real** 指真实的模型与模块装配；本轮已使用配置好的 HKU DeepSeek API、PostgreSQL/pgvector 和 Ollama embedding。测试数据来自界面已有会话，未记录 API key、个人身份信息或完整运行日志。本文只报告观察到的现象，不把 UI 上的“有证据支持”当作外部事实已被证明。

## 1. 环境与数据

- 仓库：`academic_system`；测试日期：2026-11-18；界面：`uv run streamlit run streamlit_app.py`。
- 主要模式：Real + B4，开启长期记忆与 Claim Verification；最后另以 B2、B1 对照。界面可手动改动开关，复测时须再次确认开关状态。
- 页面“文档”栏可见 `comp7608_lecture_agent.pdf`（73 页）、`comp7608_lecture_rag.pdf`（79 页）、`rag_paper.pdf`（16 页）和 `COMP7608B_Lecture09.pdf`（77 页）。前三份已由 Git 跟踪；第四份仅在本机 `data/raw/`，被 `.gitignore` 排除，不随本 PR 提交。
- PDF 页数表示原始页数；索引 chunk 数可能多于页数。引用中的页码来自 PDF 解析与检索结果，不等于模型自行推测的页码。
- 本轮记录来自真实页面，不是 `pytest` 自动化结果。回答会随模型、数据库索引和会话状态变化，复测应核对路由、证据、记忆和回答四项，而不只逐字比较答案。

## 2. 数据流与责任边界

```text
用户问题
  → Orchestrator（A）：若启用记忆，先 MemoryStore.search（B）
  → Router（A）：direct / document_retrieval / complex_task
  → Retriever（B）：解析索引的 PDF → BM25 + 向量 + RRF → Evidence（文本、来源、页码）
  → AnswerGenerator（A）：用 Evidence 与 memory_context 生成回答
  → Verifier（C）：对回答中的 claim 与 Evidence 作判断
  → MemoryManager（A）：提取值得长期保存的 MemoryCandidate
  → MemoryStore.add（B）：只有收到候选时才写 PostgreSQL
  → Streamlit（C）：显示答案、引用、校验与当前用户的长期记忆
```

`MemoryStore` 存取逻辑与“是否值得存”的决策是两层。界面选择 B4 和开启记忆，只表示**允许**读写；不会保证每条用户发言一定写入。当前默认策略是 `LLMMemoryManager`；它先通过关键词预筛，再让 LLM 生成候选并过滤。详细筛选结果应在 `AgentState.trace` 的 `memory_write` 事件中检查。用户看见“我已经记住”并不证明数据库写入成功。

## 3. 测试结果

| 编号 | 操作与观察 | 判断 |
|---|---|---|
| T01 | Real/B4 问“请问香港大学2026年世界QS排名是多少”。回答称“尚未公布”，还把 2025 版说成最新。页面提示“本轮未进行文档证据校验；回答基于模型通用知识”。 | **失败**。[香港大学官方新闻](https://www-1.hku.hk/press/press-releases/detail/28424.html)显示 2026 QS 排名已于 2025-06-19 公布，港大位列第 11。direct 路径没有实时网页工具；不能据此单独归因于模型“太差”。应标明知识可能过时，或在项目范围内明确不提供实时排名。 |
| T02 | 问“请你在每次回答问题前加上‘好的’”。回答表示理解，但拒绝以后固定加开头。随后用户报告侧栏无长期记忆。 | **失败**。偏好未被遵循或持久保存。代码中这句话不命中 `looks_memorable()` 的第一道关键词门，因此默认策略直接返回空候选；另需核查回答提示词对长期偏好的使用。 |
| T03 | 问“请告诉我什么是RAG”。给出通用解释，无页码引用；显示未进行文档证据校验。 | **符合 direct 路由**，但若演示文档问答，应明确说“根据文档”；此例不能作为 RAG 成功证据。 |
| T04 | 问“请根据文档告诉我什么是RAG”。检索到 `rag_paper.pdf` 第 6、2 页及 Agent 讲义第 46 页，生成带 `[1]–[3]` 的回答，多数 claim 得到支持，另有一条“部分支持”。 | **基本通过**。证明真实检索、英文 PDF → 中文回答、页码引用、语义校验已串联。生成的公式出现 PDF 抽取符号 `p✓`，适合记录为格式质量限制。 |
| T05 | 问“请根据上传的 RAG 论文解释 RAG 的 retriever 和 generator，并给出页码”。回答引用论文第 2、3、7 页；Verifier 多数为“有证据支持”。 | **部分通过**。第 2、3 页与主体问题相关；第 7 页的展示片段谈 n-gram diversity，与 retriever/generator 定义关系弱。需人工检查每个引用是否支撑对应句子。 |
| T06 | 问“根据英文论文，用中文解释 RAG 与 BART 的事实性比较”。回答列出论文第 6 页的 42.7%、7.1%、17%，并给出论文页码；一个较长 claim 被截断后判为“部分支持”。 | **基本通过**。跨语言检索和回答可用；Claim 拆分/截断导致校验质量下降，属于 C 的校验层。 |
| T07 | 限定“只根据已上传 PDF，告诉我 HKU 2026 年 QS 排名”。回答正确表示资料不足，但仍列出五条与排名无关的 PDF 页码；Verifier 对“资料不足”标注“有证据支持”且置信度 1.00。 | **失败（证据呈现）**。拒答本身正确；无关页码和“支持”标签可能误导用户，以为已核查全部文档。应将“未找到相关证据”与“有证据支持的事实”分开显示。 |
| T08 | 输入“请你在每次回答前加上‘好的’。，随后新问 根据文档解释 RAG 是什么？”；这一轮回答以“好的”开头。 | **不能证明长期记忆**。指令和问题在同一轮，不是跨会话回忆；当前代码对这句也未命中 `looks_memorable()`。 |
| T09 | 输入“我正在写 RAG 报告，实验部分还没有完成，请记住。”；模型回答“我已经记住”。下一轮问“我的报告还差什么？”，模型却说现有资料不足，转而检索无关 PDF。 | **失败（跨轮任务状态）**。这条输入会通过关键词预筛，但仅凭页面不能判定后续是 LLM 候选为空、存储失败、读取未召回、路由错选文档，还是生成时未采用记忆；需要核查 trace 和 `list_all(user_id)`。 |
| T10 | 相同“根据文档解释 RAG”问题在 B4、B2、B1 对照：B4/B2 显示 PDF 页码，B1 直接生成通用解释且无引用。B4 运行信息示例为 1875 tokens / 2 calls / 9218.91 ms；B2 为 1263 / 1 / 6694.15 ms；B1 为 297 / 1 / 5597.43 ms。 | **模式差异可见**。这些是单次运行样本，不是稳定均值；`cost_usd=0` 不代表 HKU API 免费，而是当前客户端未给出可计算价格。 |

## 4. 重点问题与排查顺序

### 4.1 长期记忆未生效

先在 UI 选择 **Real + B4** 并勾选“启用长期记忆”，不要用 B1/B2 复现；这两种模式默认关闭记忆。最后观察页面时，侧栏文字为“当前已关闭长期记忆”，因为最后切换到 B1。该截图不能证明 B4 时数据库为空；用户此前在 B4 观察到侧栏无记忆，仍需用下面步骤复核。

1. 用同一 `user_id` 运行“我正在写 RAG 报告，实验部分还没有完成，请记住。”。
2. 查 `state.trace` 中的 `memory_write`：`skipped`、`proposed`、`accepted`、`stored`、`error`；确认是否进入策略层及最终写入。
3. 查 `orch.memory_store.list_all(user_id)`，确认是否有 `task_state` 记录。此查询是用户自己的记忆，注意不要把内容或 `.env` 粘到公开 Issue。
4. 再用**相同** `user_id` 问“我的报告还差什么？”，核对 `state.memory_context` 与 Router 的路径。如果数据库中已有记录，但 `memory_context` 为空，再检查语义检索阈值/embedding；如果 `memory_context` 非空而回答仍无视进度，检查 AnswerGenerator prompt 与 Router。
5. 偏好复测用一句独立输入“我偏好每次回答以‘好的’开头，请记住”，然后开新会话、保留相同 `user_id` 再提文档问题。原句“请你在每次回答问题前加上……”未命中当前关键词门，因此应另作为缺陷复现。

### 4.2 缺证据时的引用与校验

T07 返回的五条只是检索器 Top-K 候选，**不代表**都与问题相关。当前 UI 只要 `state.citations` 非空就展示“页码引用”；Verifier 也可能基于这些候选将“资料不足”标成支持。建议：A 在进入生成器前判断 Evidence 相关性并记录“检索命中但无有效证据”；C 在拒答时隐藏或另列低相关候选，Verifier 不把缺乏证据解释成对“未检索到”的全库证明。阈值和提示由三人共同以 RQ1/RQ3 指标复核，避免过度过滤真正相关的段落。

### 4.3 时间敏感问题

T01 是 direct 回答，不走用户 PDF；系统当前没有 web search。即使调用真实 LLM，也不能把模型记忆中的时效性事实当成最新数据。建议 A 对排名、新闻等问题明确提示无实时检索能力，给出不确定性或要求来源；如新增 web 工具需先确认项目范围，不应把实时搜索写成当前已有功能。

## 5. 复现与验证命令

```bash
docker compose ps
ollama list
uv run python scripts/check_env.py
uv run streamlit run streamlit_app.py
```

在浏览器中选 Real/B4，按 T02、T07、T09 顺序各做一次，并在每轮展开“运行信息”。对照实验建议固定同一 PDF 索引、Top-K、模型和 `user_id`，另用 B1/B2 各跑一次文档问题。自动测试可执行：

```bash
uv run pytest -q
uv run ruff check .
git diff --check
```

修复记忆后，至少增加**同一用户跨两轮**的端到端测试：写入任务状态、断开并重新创建 Orchestrator、读取记忆并回答；再做另一个 `user_id` 的隔离测试。修复相关性/校验后，重新跑 T04–T07，并重新生成 RQ1/RQ3 的实验结果，不要把修复前后的指标混算。

## 6. 给 A、B、C 的交接

| 负责人 | 下一步 |
|---|---|
| A：`app/memory/manager.py`、`app/agent/`、Router | 先查 T02/T09 的候选漏斗、路由和记忆使用；处理 T01 的时效性表达；协调 T07 检索相关性门。 |
| B：`app/memory/postgres_store.py`、`app/retrieval/` | 仅在 trace 显示候选已接受、但 `add`/`search` 不正常时修存储；与 A 一起检查 T05/T07 的检索候选质量。 |
| C：`app/ui/`、`app/verification/` | 显示记忆写入状态及关闭/为空的区别；处理 T07 的无关引用与“无证据”校验文案；检查 T06 claim 截断。 |

团队已有 [Issue #31](https://github.com/DaLinSang/academic_system/issues/31) 追踪长期偏好写入和反馈，[Issue #32](https://github.com/DaLinSang/academic_system/issues/32) 追踪会话持久化及用户隔离；本次测试为这两项提供新的复现样本，不重复创建同类 Issue。[Issue #34](https://github.com/DaLinSang/academic_system/issues/34) 追踪无关检索候选在拒答时仍作为引用和校验依据的问题。本 PR 只提交测试文档，不关闭功能缺陷 Issue。

本测试记录是诊断材料，**没有**修改公共接口 `app/core/types.py` 或 `app/core/interfaces.py`。需要改接口时按团队三人确认流程另提 PR。
