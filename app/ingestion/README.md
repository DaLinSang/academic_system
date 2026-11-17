# PDF Ingestion 模块

> 负责人：B。Ingestion 把 PDF 转为带文件来源、页码和字符位置的 `DocumentChunk`，供 Retrieval 建立索引。

## 当前流程

```text
PDF → pypdf 逐页提取 → 清理 NUL 字符 → 页内重叠切分 → DocumentChunk[]
```

实现位于 `app/ingestion/pdf_parser.py`：

- 页码从 1 开始，保留原 PDF 页码；空白页不生成 chunk。
- 每页分别切分，默认 `chunk_size=800`、`overlap=100`；英文文本接近长度上限时优先在空格处断开，避免切断单词。找不到合适空格时仍按长度切分；重叠会重复部分上下文。
- 每个 chunk 的 `char_start` / `char_end` 是文档级字符偏移，可在解析文本中定位；清理 NUL 后的偏移不能直接作为 PDF 文件字节位置。
- chunk id 格式为 `<document_id>:p<page_number>:c<chunk_index>`。`c0` 表示该页的第一个 chunk，索引从 0 开始。
- 提取文本中的 `\x00` 会在切分前删除，因为 PostgreSQL text 不接受 NUL 字符。
- 路径不存在、扩展名不支持或 PDF 读取/页面提取失败时抛出 `IngestionError`。

## 使用方式

```python
from app.ingestion import parse_pdf

chunks = parse_pdf(
    "data/raw/rag_paper.pdf",
    document_id="rag-paper",
    chunk_size=800,
    overlap=100,
)
```

Ingestion 只解析与切分，不负责 embedding、数据库写入、检索、回答生成或 Claim Verification。实际索引通过 `HybridRetriever.index_document()` 或批量脚本完成。

## 验证

```bash
uv run pytest tests/test_pdf_parser.py -q
```

覆盖页码、空白页、多 chunk、参数校验、无效路径、解析失败及 NUL 字符清理。扫描图片型 PDF 若没有 OCR 文本层，`pypdf` 无法提取文字；需提供可搜索 PDF 或预先 OCR。
