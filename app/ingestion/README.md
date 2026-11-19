# PDF Ingestion 模块

> 负责人：B。Ingestion 把 PDF 转为带文件来源、页码和字符位置的 `DocumentChunk`，供 Retrieval 建立索引。

## 当前流程

```text
PDF → pypdf 逐页提取 → 空文字页可选 OCR → 清理 NUL 字符 → 页内重叠切分 → DocumentChunk[]
```

实现位于 `app/ingestion/pdf_parser.py`：

- 页码从 1 开始，保留原 PDF 页码；空白页不生成 chunk。
- 每页分别切分，默认 `chunk_size=800`、`overlap=100`；英文文本接近长度上限时优先在空格处断开，避免切断单词。找不到合适空格时仍按长度切分；重叠会重复部分上下文。
- 每个 chunk 的 `char_start` / `char_end` 是文档级字符偏移，可在解析文本中定位；清理 NUL 后的偏移不能直接作为 PDF 文件字节位置。
- chunk id 格式为 `<document_id>:p<page_number>:c<chunk_index>`。`c0` 表示该页的第一个 chunk，索引从 0 开始。
- 提取文本中的 `\x00` 会在切分前删除，因为 PostgreSQL text 不接受 NUL 字符。
- 路径不存在、扩展名不支持或 PDF 读取/页面提取失败时抛出 `IngestionError`。
- `ocr_mode="auto"`（默认）只对提取不到文字的页面尝试 OCR；`off` 完全关闭；`force` 对每页使用 OCR。OCR 后仍保留原 PDF 页码和 `chunk_id` 规则。

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

### 扫描件和 OCR

OCR 是“光学字符识别”：先把 PDF 页面渲染为图片，再从图片里识别文字。`pypdf` 只读取已有文字层。当前可选路径使用系统命令 `pdftoppm`（Poppler）和 `tesseract`；普通文字 PDF 完全不需要这两个工具。若未安装，默认 `auto` 会保留原有空页跳过行为，`force` 会明确报错。若整篇 PDF 均无可搜索文字，索引器会拒绝导入并提示安装 OCR 工具，避免误以为已成功入库。

在 macOS 上安装后先验证命令是否可见：

```bash
brew install poppler tesseract
which pdftoppm tesseract
tesseract --list-langs
```

默认识别英语（`eng`）。若 PDF 中有中文，需另外安装 Tesseract 的中文语言数据，并在运行索引前设 `PDF_OCR_LANG=eng+chi_sim`；语言包名称以 `tesseract --list-langs` 实际输出为准。已有索引不会自动更新，安装 OCR 后要重新执行 `uv run python scripts/index_pdfs.py`。OCR 文字可能错字，公式、表格和复杂双栏排版尤其需要人工核对引用页面。当前机器没有安装 Tesseract，因此本 PR 自动测试只验证 OCR 调用路径与页码保留，尚未做真实扫描 PDF 的端到端识别测试。

## 验证

```bash
uv run pytest tests/test_pdf_parser.py -q
```

覆盖页码、空白页、多 chunk、参数校验、无效路径、解析失败、NUL 字符清理，以及 OCR 空页和缺少系统工具时的行为。
