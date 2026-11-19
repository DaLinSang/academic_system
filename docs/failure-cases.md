# Failure Cases

## FC-001 中文回答与英文证据的词面误判

- Issue：#30
- 修复前 claim：RAG 将检索到的外部文档与语言模型生成过程结合起来。
- 英文 Evidence：Retrieval-Augmented Generation combines retrieved external documents with a pre-trained language model for generation.
- 修复前结果：LexicalVerifier token overlap = 0，status = unsupported，confidence = 1.00。
- 根因：中文双字 token 与英文单词没有交集；1.00 实际来自 1 - overlap，被 UI 误读为事实错误概率。
- 修复后：Real 链路使用 SemanticVerifier 按语义判断，可返回 supported 并绑定真实 chunk_id；LexicalVerifier 仅保留为基线和降级路径。

## FC-002 direct 回答没有文档 Evidence

- 修复前：Verifier 对每句话生成 unsupported，UI 显示“缺少证据”。
- 根因：未进行文档校验与证据明确不支持使用了同一状态展示。
- 修复后：Orchestrator 在 Evidence 为空时跳过 Verification。direct 回答显示“未进行文档证据校验；回答基于模型通用知识”；检索未命中显示单独警告。

## FC-003 T06 分号导致 claim 上下文截断

- 输入：RAG 与 BART 的事实性比较，包含 42.7%、7.1% 和 17% 等同一句数据。
- 修复前：claim splitter 在中文/英文分号处切分，后半句只剩数字，失去 RAG、BART 和比较关系，SemanticVerifier 容易判为部分支持。
- 修复后：分号保留在同一 claim 内，只在句号、问号、感叹号和换行处结束 claim；新增 T06 百分比回归测试。

## FC-004 T07 资料不足仍展示无关页码

- Issue：#34
- 输入：只根据已上传 PDF，询问 HKU 2026 年 QS 排名。
- 修复前：回答正确拒答，但 UI 展示全部 Top-K 候选页码；“资料不足”也被当作普通事实 claim 校验。
- 修复后：拒答/absence statement 不进入事实 claim 校验；UI 只展示回答正文实际使用的引用，资料不足时隐藏候选页码并提示“未找到支持本问题的文档证据”。
- 正常 RAG 回答仍可展示明确使用的第 2/3 页等引用。

## RQ3 影响

- 修复改变 Verification 算法，旧 B1-B4 结果不可与新结果混用。
- 正式实验必须全部重跑三次。
- UNVERIFIED claim 单独统计，不进入 claim support rate 分母。
- SemanticVerifier 的模型调用 token、成本和延迟已归集到 AgentState.usage，并进入 RQ4。
- T06/T07 改变 claim 分母与引用展示，正式结果需要重新运行，不能与修复前数据混用。

## FC-005 RQ4 latency 重复累计

- 发现方式：2026-11-19 Real B4 smoke，单样本实际墙钟约 55 秒，但
  `AgentState.usage.latency_ms` 约 111 秒。
- 根因：组件 LLM latency（包含 retry 等待）与整条 pipeline 墙钟再次相加。
- C 侧处理：RQ4 改为读取 pipeline start/end trace timestamp；组件耗时只用于
  trace 诊断，不再作为总延迟再次累计。

## FC-006 小模型生成格式污染事实 claim

- 环境：本地 Ollama `llama3.2:1b`，Real B4 单样本 q001。
- 现象：答案重复“来源编号 / 规则编号 / 提问”等提示结构；Verifier 将
  “来源：rag_paper.pdf 第 1 页”这类元信息拆成 supported claim。
- 影响：检索命中正确，但答案质量、claim 分母和 token 数不能代表正式模型。
- 结论：本地 1B 模型只用于 Real 链路 smoke，不作为正式 B1-B4 研究结果；
  正式 4x3 实验应使用团队冻结的聊天模型配置。

## 2026-11-19 实验状态

- 50 条数据集的 Mock 4x3 已生成 12 个 run，meta 五项上下文与 metrics 完整，
  用于验证 runner、对齐、聚合和输出结构。
- Real B4 q001 已跑通：pgvector、Ollama embedding、PDF 索引、Router、Retriever、
  AnswerGenerator 和 SemanticVerifier 均实际执行。
- 完整 Real 4x3 未用本地 1B 模型冒充正式结果；团队聊天模型 key 冻结后可直接
  使用 `--real --repeats 3` 生成最终表格。
