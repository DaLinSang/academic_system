"""PDF parsing and text chunking utilities."""

from pathlib import Path

from pypdf import PdfReader

from app.core.errors import IngestionError
from app.core.types import DocumentChunk

DEFAULT_CHUNK_SIZE = 800
DEFAULT_OVERLAP = 100

def split_text(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> list[tuple[str, int, int]]:
    """Split text into overlapping chunks.

    Returns:
        A list of tuples containing:
        - chunk text
        - start character offset
        - end character offset
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than zero")

    if overlap < 0:
        raise ValueError("overlap cannot be negative")

    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")

    normalized_text = text.strip()

    if not normalized_text:
        return []

    chunks: list[tuple[str, int, int]] = []
    text_length = len(normalized_text)
    start = 0

    while start < text_length:
        end = min(start + chunk_size, text_length)
        chunk_text = normalized_text[start:end]

        if chunk_text.strip():
            chunks.append((chunk_text, start, end))

        if end == text_length:
            break

        start = end - overlap

    return chunks


def parse_pdf(
    pdf_path: str,
    document_id: str | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> list[DocumentChunk]:
    """Parse a PDF page by page and create text chunks."""
    if chunk_size <= 0:
        raise IngestionError("chunk_size must be greater than zero")

    if overlap < 0 or overlap >= chunk_size:
        raise IngestionError(
            "overlap must be non-negative and smaller than chunk_size"
        )

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
    document_char_offset = 0

    for page_number, page in enumerate(reader.pages, start=1):
        try:
            raw_text = page.extract_text() or ""
        except Exception as exc:
            raise IngestionError(
                f"Failed to extract page {page_number} from {pdf_path}"
            ) from exc

        page_text = raw_text.strip()

        leading_whitespace = len(raw_text) - len(raw_text.lstrip())
        page_char_start = document_char_offset + leading_whitespace

        # Add one separator character between pages in the document offset.
        document_char_offset += len(raw_text) + 1

        if not page_text:
            continue

        page_chunks = split_text(
            page_text,
            chunk_size=chunk_size,
            overlap=overlap,
        )

        for chunk_index, (chunk_text, local_start, local_end) in enumerate(
            page_chunks
        ):
            chunks.append(
                DocumentChunk(
                    chunk_id=(
                        f"{resolved_document_id}:"
                        f"p{page_number}:"
                        f"c{chunk_index}"
                    ),
                    document_id=resolved_document_id,
                    file_name=path.name,
                    page_number=page_number,
                    text=chunk_text,
                    char_start=page_char_start + local_start,
                    char_end=page_char_start + local_end,
                )
            )

    return chunks
