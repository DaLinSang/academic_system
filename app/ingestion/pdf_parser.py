'''
这里暂时“一页一个 chunk”，先完成页码和数据结构。真正的文本切分会在后续阶段完善。
'''
from pathlib import Path

from pypdf import PdfReader

from app.core.errors import IngestionError
from app.core.types import DocumentChunk


def parse_pdf(
    pdf_path: str,
    document_id: str | None = None,
) -> list[DocumentChunk]:
    """逐页解析 PDF，并保留原始页码。"""
    path = Path(pdf_path)

    if not path.is_file():
        raise IngestionError(f"PDF file does not exist: {pdf_path}")

    if path.suffix.lower() != ".pdf":
        raise IngestionError(f"Unsupported file type: {path.suffix}")

    resolved_document_id = document_id or path.stem

    try:
        reader = PdfReader(str(path))
    except Exception as exc:
        raise IngestionError(f"Failed to open PDF: {pdf_path}") from exc

    chunks: list[DocumentChunk] = []

    for page_number, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            raise IngestionError(
                f"Failed to extract page {page_number} from {pdf_path}"
            ) from exc

        if not text.strip():
            continue

        chunks.append(
            DocumentChunk(
                chunk_id=f"{resolved_document_id}:p{page_number}:c0",
                document_id=resolved_document_id,
                file_name=path.name,
                page_number=page_number,
                text=text.strip(),
            )
        )

    return chunks
