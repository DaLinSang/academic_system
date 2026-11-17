# B 模块 Demo 与调用说明

本说明供 A、C 复现 B 提供的 PDF 检索和长期记忆能力。代码与文档为英文文件名/英文代码；本说明及团队沟通可用中文。Demo 问题可用中英文，但无 API key 时 Router 会退回当前关键词规则，答案生成也会使用固定 Mock 文本，不能据此评价真实答案质量。

## 1. Demo PDFs

将三份本地 PDF 放在仓库 `data/raw/`，这些课程/论文文件不提交到 Git：

| 文件 | 用途 |
|---|---|
| `comp7608_lecture_rag.pdf` | RAG 课程内容主 Demo |
| `comp7608_lecture_agent.pdf` | 与其他讲义做多文档比较 |
| `rag_paper.pdf` | 16 页文字型论文，长文本检索与页码引用测试 |

已做本地解析检查：论文首页和第二页分别提取出 2,988、4,570 个字符；两份课件约 73/79 页，含大量图示，页面可提取文本量较少。此次新切分器本地解析得到 74、79、92 chunks（依次为 Agent 讲义、RAG 讲义、RAG 论文），三份文件都没有 NUL 字符残留。不同 PDF 或解析器版本可能让计数变化。

本次切分逻辑更新后，建议重新运行索引脚本，再记录新的 chunk 数量；原计数仅是更新前的本地观测，不作为验收目标。PDF 应选可复制文字的版本；图片页需要先 OCR，当前解析器不会识别图片里的字。

## 2. 启动前检查

```bash
docker compose up -d
docker compose ps
ollama list
curl --noproxy '*' http://localhost:11434/api/tags
uv run python scripts/check_env.py
```

PostgreSQL 应显示 `healthy`，Ollama 应能列出 `nomic-embed-text`。本机外部 VPN/代理可能使 Ollama localhost 请求返回 502；此前关闭外部 VPN 后用户测试恢复正常。`scripts/check_env.py` 与 Retrieval/Memory 请求的代理设置可能不同，若仅自检失败、而直连 curl 成功，应把自检结果单独报告，不要据此判断 PDF 索引一定失败。

## 3. 检查与建立索引

```bash
uv run python scripts/index_pdfs.py --list
uv run python scripts/index_pdfs.py
```

索引流程：

```text
data/raw/*.pdf
→ app/ingestion/pdf_parser.py
→ DocumentChunk（含 file_name、page_number）
→ HybridRetriever.index_document()
→ BM25 + Ollama embedding + PostgreSQL/pgvector
```

索引脚本对每份文件打印 chunk 数；只要有文件失败，进程以非零状态结束。重复索引依赖 chunk 主键 upsert，预期不会新增重复 chunk。
重新索引同一 PDF 会替换旧 chunk；如果本次分块变化，旧 chunk 不会留在该文档索引中。真实论文曾因提取文本包含 `NUL` 导致 PostgreSQL 拒绝写入；解析器现在会清理该字符。

## 4. 推荐测试顺序

先用 B2 测检索，B2 不运行 Router、Memory 或 Verifier，会直接检索：

```bash
uv run python -m app.demo --real -m B2 -q "According to the paper, what problem does RAG solve?"
```

检查 `④ 检索证据` 是否出现 `rag_paper.pdf` 和页码。再测试多文档问题：

```bash
uv run python -m app.demo --real -m B2 -q "Compare the RAG explanations in the lecture slides and the paper."
```

最后运行 B4 检查完整链路：

```bash
uv run python -m app.demo --real -m B4 -q "这篇论文解决了什么问题？"
```

无 API key 时，英文 B4 问题可能被 Router 的中文关键词降级规则判成 `direct`，因而跳过检索。中文“这篇论文……”命中当前降级词后通常进入 `document_retrieval`。这是 Router 降级策略的限制，已建议向 A 提 Issue；若专门验证 Retrieval，请先用 B2。

## 5. 怎么解释 Mock 输出

`--real` 表示装配真实 Retrieval/Memory 等模块，不代表必然使用真实聊天模型。未配置可用 LLM API key 时，`get_llm()` 返回 `MockLLM`；它对不同输入返回固定 canned answer。因此此时可验证真实 PDF 是否被索引、Hybrid Retrieval 是否返回 Evidence、页码和数据库链路是否正常；不能验证真实答案是否理解问题或忠实引用证据。

若固定 Mock 答案与本轮真实 Evidence 无关，Verifier 将相关 claim 标成 `unsupported` 是预期保护行为，不足以证明 C 的 Verifier 有 bug。获得 API 访问后再复测 B3/B4 的真实 Router、生成与引用。

## 6. 给 A/C 的接口

### A：调用 Retrieval

通过 `Retriever` Protocol 调 `retrieve(query, top_k)`，使用 `Evidence` 的 `text`、`file_name`、`page_number`、`chunk_id` 和 `score`。不要导入 B 的数据库内部实现或直接查询表。

### C：展示 Evidence 与管理 Memory

UI 可从 `AgentState.evidence` 展示来源文件、页码和文本；Verifier 的 `supporting_evidence` 应指向真实 `chunk_id`。长期记忆管理通过 `MemoryStore.list_all(user_id)`、`update()`、`delete()`；关闭记忆使用 Orchestrator 的 `enable_memory=False`。当前协议没有批量清空方法，清空可先列出该用户条目再逐个删除，但删除 ID 必须来自当前用户的列表。

```python
from app.agent.factory import build_stage1_orchestrator

orch = build_stage1_orchestrator()
state = orch.run("这篇论文解决了什么问题？", user_id="demo-user")
for item in state.evidence:
    print(item.file_name, item.page_number, item.text)
```

真实装配需要数据库和 Ollama；Mock 装配使用 `build_mock_orchestrator()`，不读本地 PDF。

## 7. 输出记录模板

每次 Demo 记录：模式、命令、是否配置真实 LLM、Router 路径、Evidence 文件/页码、答案是否 Mock、Verifier 状态、tokens/calls/latency、错误。Mock 环境的 token 是估算值且成本为零；不要和真实 API 用量混在同一实验结果中。
