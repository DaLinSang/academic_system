# B Final: document summary, scoped retrieval and deletion

## What changed

Single-PDF summary requests now use persisted page text for the named file instead of representing the full document with a small Top-K sample. Ordinary factual questions continue using BM25 + pgvector + RRF and page citations. Documents over the 50,000-character safety limit fall back to the normal evidence path.

## B interfaces

`Retriever` now exposes `read_document(file_name)` and `delete_document(file_name)`. The first returns OCR/parsed text in page order. The second removes chunks, vectors and the persisted page cache together. A and C should use these injected methods rather than accessing PostgreSQL directly.

## Persistence flow

```text
PDF/OCR -> DocumentChunk -> document_chunks + document_pages
                                  |                 |
                            BM25/vector RAG     full-text summary
```

`document_pages` stores cleaned text for every indexed page and survives Streamlit reruns and process restarts. The existing chunk/vector table remains the source for ordinary retrieval and citations.

The page cache reconstructs text from chunk offsets, removing the deliberate chunk overlap. `read_document()` includes page labels; `delete_document()` clears both database tables and invalidates the current retriever's BM25 cache. It does not remove the uploaded PDF from the filesystem; the upload owner must handle that separately.

## How A should use it

The orchestrator detects an explicit single-PDF summary and loads the full text before generation. The UI can make this deterministic by passing the selected filename. Full-text summaries must not invent page citations; factual questions should remain on the cited RAG path.

Examples: `请总结 rag_paper.pdf 的研究问题、方法、结果和局限。` and `请分析 Chapter9-AssociationRule.pdf，并整理一份复习提纲。`

## OCR setup

macOS Homebrew:

```bash
brew install poppler tesseract
brew install tesseract-lang
which pdftoppm pdfinfo tesseract
tesseract --list-langs
```

The language list should include `eng` and, for Chinese scans, `chi_sim`. Set `PDF_OCR_LANG=eng+chi_sim` in `.env` when needed. Windows users install Poppler and add its `Library/bin` directory to PATH, install Tesseract and add its directory to PATH, then run `where pdftoppm`, `where pdfinfo`, and `tesseract --list-langs`. Docker/CI should provide the same executables.

## Verification

```bash
uv run ruff check app tests
uv run pytest -q
uv run python scripts/index_pdfs.py --list
uv run python scripts/index_pdfs.py
uv run python -m app.demo --real -m B4 -q "请总结 rag_paper.pdf 的研究问题、方法、结果和局限。"
```

Expected observations are a `document_read` trace for a named summary, ordinary page citations for RAG questions, removal from document status after deletion, and recreation of both caches after reindexing.

## Handoff

- **B** owns parsing/OCR, page cache, chunk/vector persistence, scoped retrieval and deletion.
- **A** owns Router/Planner and should expose the selected filename when the user means “this uploaded PDF”.
- **C** owns Claim Verification/UI presentation. Full-text summaries should not show fake citations; RAG answers keep normal citations and verification.
- Web Search remains independent and should not run for a local PDF summary unless explicitly requested.
