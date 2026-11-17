# Retrieval 模块

> 成员 B 负责。验证人：A。<br>
> 本模块把文档 chunks 转换为带来源页码的 `Evidence`，供 Agent 回答和 Claim Verification 使用。

## 1. 最终检索流程

```text
PDF
    ↓
app.ingestion.parse_pdf()
    ↓
DocumentChunk
    ├── BM25 lexical index
    └── Ollama embedding → PostgreSQL/pgvector
                 ↓
        BM25 ranked list + vector ranked list
                 ↓
              RRF fusion
                 ↓
              Evidence[]
```

当前模块包含三个层次：

- `KeywordRetriever`：关键词重合度基线，保留用于回归测试和 Mock 对照；
- `BM25Retriever`：使用 `rank_bm25` 的词法检索；
- `HybridRetriever`：结合 BM25、Ollama embedding、pgvector 和 RRF 的最终检索器。

## 2. 模块职责

- 调用 ingestion 解析 PDF；
- 建立 BM25 文本索引；
- 调用 Ollama 生成 chunk 和 query embedding；
- 将 chunk embedding 幂等写入 PostgreSQL/pgvector；
- 执行向量相似度检索；
- 使用 Reciprocal Rank Fusion 融合 BM25 和向量结果；
- 将结果统一转换成公共结构 `Evidence`；
- 保留 `chunk_id`、`file_name` 和 `page_number`。

## 3. 公共调用方式

```python
from app.retrieval import HybridRetriever

retriever = HybridRetriever()
retriever.index_document(
    "data/raw/demo.pdf",
    document_id="demo-paper",
)

evidence = retriever.retrieve(
    "What is retrieval augmented generation?",
    top_k=5,
)
```

`index_document()` 的流程是：

```text
PDF
→ parse_pdf()
→ DocumentChunk
→ Ollama embedding
→ PostgreSQL upsert
→ 标记 BM25 缓存失效，下次检索从数据库重建全量索引
```

`retrieve()` 的流程是：

```text
query
→ BM25 检索
→ query embedding
→ pgvector 检索
→ RRF 融合
→ Evidence[]
```

## 4. 公共接口

实现 `app/core/interfaces.py` 中的 `Retriever` Protocol：

```python
def retrieve(query: str, top_k: int = 5) -> list[Evidence]
def index_document(pdf_path: str, document_id: str | None = None) -> int
```

所有返回结果都包含：

```text
chunk_id
document_id
file_name
page_number
text
score
retrieval_method
```

## 5. BM25 与向量检索

BM25 适合匹配：

- 专有名词；
- 精确关键词；
- 公式、缩写和课程术语。

向量检索适合匹配：

- 同义表达；
- 语义相近但用词不同的句子；
- 中文和英文之间的语义关联。

两种检索结果不直接比较原始分数，而是分别排序后使用 RRF：

```text
RRF score = bm25_weight / (rrf_k + bm25_rank)
          + vector_weight / (rrf_k + vector_rank)
```

默认配置：

```text
rrf_k = 60
bm25_weight = 1.0
vector_weight = 1.0
```

最终结果使用：

```python
RetrievalMethod.RRF
```

## 6. PostgreSQL 表

表结构位于：

```text
app/retrieval/schema.sql
```

主要字段：

| 字段 | 用途 |
|---|---|
| `chunk_id` | 主键，保证重复导入可更新 |
| `document_id` | 文档标识 |
| `file_name` | PDF 文件名 |
| `page_number` | 原始页码 |
| `text` | chunk 文本 |
| `char_start` / `char_end` | 文本偏移 |
| `embedding` | pgvector 向量 |

重复导入同一文档时，先在同一事务中删除该文档旧 chunk，再写入新的 chunk；避免切分数量变化后遗留旧证据。索引成功后下一次检索会重新加载数据库中**所有文档**的 BM25 数据。向量候选对应的证据批量读取，避免逐条数据库查询。

## 7. 配置

配置统一来自 `app/core/config.py`：

```env
OLLAMA_BASE_URL=http://localhost:11434
EMBEDDING_MODEL=nomic-embed-text
EMBEDDING_DIM=768

POSTGRES_HOST=localhost
POSTGRES_PORT=5434
POSTGRES_DB=academic
POSTGRES_USER=academic
POSTGRES_PASSWORD=academic_dev_pw
```

启动数据库：

```bash
docker compose up -d
docker compose ps
```

确认 PostgreSQL 状态为 `healthy` 后再进行真实索引。

确认 Ollama embedding：

```bash
ollama serve
ollama pull nomic-embed-text
uv run python scripts/check_env.py
```

## 8. 错误处理

`OllamaEmbedder.embed()` 调用本机 `http://localhost:11434/api/embeddings`。若本机设置了 HTTP 代理，普通 Python 请求可能被送往代理，出现 `502 Bad Gateway`，即使 Ollama 本身运行正常。本实现对本地 Ollama 请求使用 `trust_env=False`，避免继承代理环境；这属于 B 的检索实现，无需修改 A 的公共 `Retriever` 接口。

排查顺序：先用 `ollama list` 确认模型存在，再用 `curl --noproxy '*' http://localhost:11434/api/tags` 确认本机服务可达，最后检查 `OLLAMA_BASE_URL` 是否仍指向本机。`ollama serve` 仅在服务没启动时运行；`ollama pull nomic-embed-text` 仅在模型缺失时运行。

| 情况 | 行为 |
|---|---|
| 空 query | 返回 `[]` |
| `top_k <= 0` | 返回 `[]` |
| 没有命中 | 返回 `[]` |
| PDF 解析失败 | 抛出 `IngestionError` |
| Ollama 不可用 | 转换为 `RetrievalError` |
| PostgreSQL 不可用 | 转换为 `RetrievalError` |
| 重复导入 | 按 `chunk_id` 更新 |

跨模块边界不会暴露 `psycopg` 或 `httpx` 的底层异常。

## 9. 测试

运行全部测试：

```bash
uv run pytest -q
uv run ruff check .
git diff --check
```

测试覆盖：

- BM25 正常检索；
- 向量结果与 BM25 结果的 RRF 融合；
- `top_k`；
- 空 query；
- 无命中；
- Evidence 字段和页码；
- PDF 导入；
- 重复导入；
- 错误转换。

## 10. 与其他模块的关系

```text
app/ingestion
    ↓ DocumentChunk
app/retrieval
    ↓ Evidence
app/agent / app/orchestrator
    ↓ draft answer
app/verification
    ↓ ClaimCheck[]
```

A 只需要通过 `Retriever` 接口调用本模块，不需要操作数据库或导入内部实现类。C 只读取 `Evidence`，使用其中的 `text`、`chunk_id`、`file_name` 和 `page_number` 进行 claim verification。
