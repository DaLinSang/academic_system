# Retrieval 模块

> 成员 B 负责。验证人：A。<br>
> 本模块负责把 `DocumentChunk` 转换为带来源页码的 `Evidence`，供 Agent 和 Verifier 使用。

## 1. 当前版本定位

当前模块包含两个检索实现：

```text
PDF
    ↓
app.ingestion.parse_pdf()
    ↓
DocumentChunk
    ├── KeywordRetriever：基础关键词匹配
    └── BM25Retriever：正式 BM25 排序
                 ↓
              Evidence
```

`BM25Retriever` 是当前正式的文本检索实现。`KeywordRetriever` 保留用于基线比较、Mock 演示和回归测试。

## 2. 当前职责

- 接收 ingestion 生成的 `DocumentChunk`；
- 解析 PDF 并把 chunks 放入内存索引；
- 根据 query 做关键词匹配或 BM25 排序；
- 按相关分数从高到低返回结果；
- 将 `DocumentChunk` 转换成公共结构 `Evidence`；
- 保留 `chunk_id`、`file_name` 和 `page_number`；
- 支持 `top_k`；
- 无命中时返回空列表；
- 重复导入同一文档时替换旧版本。

## 3. 当前不负责

- PostgreSQL / pgvector；
- Ollama 或其他 embedding 模型；
- RRF 混合排序；
- Agent 路由和回答生成；
- Claim Verification。

## 4. 公共调用方式

```python
from app.retrieval import BM25Retriever

retriever = BM25Retriever()
count = retriever.index_document("data/raw/demo.pdf", document_id="demo-paper")
evidence = retriever.retrieve("retrieval augmented generation", top_k=5)
```

`index_document()` 返回导入的 chunk 数量。

`retrieve()` 返回：

```python
list[Evidence]
```

## 5. BM25 分数规则

`BM25Retriever` 使用 `rank_bm25.BM25Okapi` 对所有已导入 chunks 建立内存索引。返回结果按 BM25 score 降序排列：

```text
query
    ↓
tokenize
    ↓
BM25Okapi.get_scores()
    ↓
Evidence(score=BM25 score)
```

BM25 结果使用：

```python
RetrievalMethod.BM25
```

关键词基线使用 `RetrievalMethod.MOCK`，两者不能混淆。

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

测试覆盖 BM25 正常检索、无命中、`top_k`、空 query、页码保留、PDF 导入和重复导入。

## 8. 后续计划

1. 增加 embedding 和 pgvector 索引；
2. 融合 BM25 与向量结果；
3. 使用 RRF 生成最终排序；
4. 增加真实 PDF 和数据库集成测试。
