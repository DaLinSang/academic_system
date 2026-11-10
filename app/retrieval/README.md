# Retrieval 模块

> 成员 B 负责。验证人：A。<br>
> 本模块负责把 `DocumentChunk` 转换为带来源页码的 `Evidence`，供 Agent 和 Verifier 使用。

## 1. 当前版本定位

当前 PR 实现一个不依赖数据库和外部模型的内存关键词检索器：

```text
PDF
    ↓
app.ingestion.parse_pdf()
    ↓
DocumentChunk
    ↓
KeywordRetriever
    ↓
关键词重合度排序
    ↓
Evidence
```

这是 retrieval 的最小可用版本，目的是先验证模块接口、页码引用和端到端数据流。

## 2. 当前职责

- 接收 ingestion 生成的 `DocumentChunk`；
- 解析 PDF 并把 chunks 放入内存索引；
- 根据 query 做简单关键词匹配；
- 按相关分数从高到低返回结果；
- 将 `DocumentChunk` 转换成公共结构 `Evidence`；
- 保留 `chunk_id`、`file_name` 和 `page_number`；
- 支持 `top_k`；
- 无命中时返回空列表；
- 重复导入同一文档时替换旧版本。

## 3. 当前不负责

- PostgreSQL / pgvector；
- Ollama 或其他 embedding 模型；
- BM25；
- RRF 混合排序；
- Agent 路由和回答生成；
- Claim Verification。

## 4. 公共调用方式

```python
from app.retrieval import KeywordRetriever

retriever = KeywordRetriever()
count = retriever.index_document("data/raw/demo.pdf", document_id="demo-paper")
evidence = retriever.retrieve("retrieval augmented generation", top_k=5)
```

`index_document()` 返回导入的 chunk 数量。

`retrieve()` 返回：

```python
list[Evidence]
```

## 5. 分数规则

当前分数是 query 中命中的不同 token 数量除以 query token 总数：

```text
score = matched_query_tokens / total_query_tokens
```

当前版本使用 `RetrievalMethod.MOCK` 表示阶段性内存检索。后续真正接入 BM25 后，检索方法会改为 `RetrievalMethod.BM25`。

## 6. 错误和边界行为

| 情况 | 行为 |
|---|---|
| 空 query | 返回 `[]` |
| `top_k <= 0` | 返回 `[]` |
| 没有匹配结果 | 返回 `[]` |
| PDF 不存在或解析失败 | 由 ingestion 抛出 `IngestionError` |
| 重复导入同一文档 | 替换原来的 chunks |

## 7. 测试

```bash
uv run pytest tests/test_keyword_retriever.py -v
uv run pytest -q
uv run ruff check .
```

测试覆盖正常检索、无命中、`top_k`、空 query、页码保留、PDF 导入和重复导入。

## 8. 后续计划

1. 用 BM25 替换当前关键词重合度；
2. 增加 embedding 和 pgvector 索引；
3. 融合 BM25 与向量结果；
4. 使用 RRF 生成最终排序；
5. 增加真实 PDF 和数据库集成测试。
