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

## RQ3 影响

- 修复改变 Verification 算法，旧 B1-B4 结果不可与新结果混用。
- 正式实验必须全部重跑三次。
- UNVERIFIED claim 单独统计，不进入 claim support rate 分母。
- SemanticVerifier 的模型调用 token、成本和延迟已归集到 AgentState.usage，并进入 RQ4。
