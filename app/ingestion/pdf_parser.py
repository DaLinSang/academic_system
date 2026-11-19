"""PDF parsing and text chunking utilities."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

from pypdf import PdfReader

from app.core.errors import IngestionError
from app.core.types import DocumentChunk

DEFAULT_CHUNK_SIZE = 800
DEFAULT_OVERLAP = 100


def _ocr_page(pdf_path: Path, page_number: int, *, required: bool) -> str:
    """Read an image-only PDF page using optional Poppler and Tesseract CLIs."""
    if not shutil.which("pdftoppm") or not shutil.which("tesseract"):
        if required:
            raise IngestionError("OCR requires pdftoppm and tesseract on PATH")
        return ""

    try:
        with tempfile.TemporaryDirectory(prefix="academic-ocr-") as temp_dir:
            image_prefix = Path(temp_dir) / "page"
            subprocess.run(
                [
                    "pdftoppm", "-f", str(page_number), "-l", str(page_number),
                    "-singlefile", "-r", "200", "-png", str(pdf_path),
                    str(image_prefix),
                ],
                check=True,
                capture_output=True,
                timeout=90,
            )
            result = subprocess.run(
                [
                    "tesseract", str(image_prefix.with_suffix(".png")), "stdout",
                    "-l", os.environ.get("PDF_OCR_LANG", "eng"),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=90,
            )
            return result.stdout.replace("\x00", "")
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", errors="replace")
        raise IngestionError(
            f"OCR failed on page {page_number} of {pdf_path.name}: {detail[:500]}. "
            "Check tesseract --list-langs and PDF_OCR_LANG; install poppler and tesseract."
        ) from exc


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
        # Prefer a nearby word boundary in text-heavy PDFs; preserve the
        # original characters so offsets still point into the extracted page.
        if end < text_length and normalized_text[end - 1].isalnum() and normalized_text[end].isalnum():
            boundary = normalized_text.rfind(" ", start + chunk_size * 3 // 4, end)
            if boundary > start:
                end = boundary + 1
        chunk_text = normalized_text[start:end]

        if chunk_text.strip():
            chunks.append((chunk_text, start, end))

        if end == text_length:
            break

        next_start = max(start + 1, end - overlap)
        if (
            next_start < text_length
            and normalized_text[next_start - 1].isalnum()
            and normalized_text[next_start].isalnum()
        ):
            boundary = normalized_text.rfind(
                " ", max(start, next_start - chunk_size // 4), next_start
            )
            if boundary >= start:
                next_start = boundary + 1
        start = next_start

    return chunks


def parse_pdf(
    pdf_path: str,
    document_id: str | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
    ocr_mode: Literal["auto", "off", "force"] = "auto",
) -> list[DocumentChunk]:
    """Parse pages into chunks; optionally OCR pages with no text layer."""
    if ocr_mode not in ("auto", "off", "force"):
        raise IngestionError("ocr_mode must be auto, off, or force")
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
            raw_text = raw_text.replace("\x00", "")
        except Exception as exc:
            raise IngestionError(
                f"Failed to extract page {page_number} from {pdf_path}"
            ) from exc

        if ocr_mode == "force" or (ocr_mode == "auto" and not raw_text.strip()):
            ocr_text = _ocr_page(path, page_number, required=ocr_mode == "force")
            if ocr_text.strip():
                raw_text = ocr_text

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
