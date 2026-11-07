"""PDF ingestion parser tests."""

from __future__ import annotations

import pytest

from app.core.errors import IngestionError
from app.ingestion import pdf_parser


class FakePage:
    """Fake PDF page used in parser tests."""

    def __init__(self, text: str) -> None:
        self.text = text

    def extract_text(self) -> str:
        """Return fake page text."""
        return self.text


class BrokenPage:
    """Fake PDF page that raises an extraction error."""

    def extract_text(self) -> str:
        """Raise a fake extraction error."""
        raise RuntimeError("page extraction failed")


class FakeReader:
    """Fake reader used to replace pypdf.PdfReader."""

    def __init__(self, pages: list[object]) -> None:
        self.pages = pages


def test_split_text_returns_empty_list_for_blank_text() -> None:
    chunks = pdf_parser.split_text("   ")

    assert chunks == []


def test_split_text_returns_one_chunk_for_short_text() -> None:
    chunks = pdf_parser.split_text(
        "short text",
        chunk_size=100,
        overlap=10,
    )

    assert chunks == [("short text", 0, 10)]


def test_split_text_creates_overlapping_chunks() -> None:
    chunks = pdf_parser.split_text(
        "abcdefghij",
        chunk_size=6,
        overlap=2,
    )

    assert chunks == [
        ("abcdef", 0, 6),
        ("efghij", 4, 10),
    ]


def test_split_text_rejects_invalid_chunk_size() -> None:
    with pytest.raises(ValueError):
        pdf_parser.split_text(
            "some text",
            chunk_size=0,
            overlap=0,
        )


def test_split_text_rejects_invalid_overlap() -> None:
    with pytest.raises(ValueError):
        pdf_parser.split_text(
            "some text",
            chunk_size=10,
            overlap=10,
        )


def test_parse_pdf_preserves_page_numbers(monkeypatch, tmp_path) -> None:
    pdf_path = tmp_path / "demo.pdf"
    pdf_path.write_bytes(b"fake pdf content")

    pages = [
        FakePage("First page content"),
        FakePage("Second page content"),
    ]

    monkeypatch.setattr(
        pdf_parser,
        "PdfReader",
        lambda _: FakeReader(pages),
    )

    chunks = pdf_parser.parse_pdf(
        str(pdf_path),
        document_id="demo-paper",
    )

    assert len(chunks) == 2
    assert chunks[0].page_number == 1
    assert chunks[1].page_number == 2
    assert chunks[0].file_name == "demo.pdf"
    assert chunks[0].document_id == "demo-paper"
    assert chunks[0].chunk_id == "demo-paper:p1:c0"
    assert chunks[1].chunk_id == "demo-paper:p2:c0"


def test_parse_pdf_creates_multiple_chunks_for_long_page(
    monkeypatch,
    tmp_path,
) -> None:
    pdf_path = tmp_path / "demo.pdf"
    pdf_path.write_bytes(b"fake pdf content")

    pages = [FakePage("abcdefghij")]

    monkeypatch.setattr(
        pdf_parser,
        "PdfReader",
        lambda _: FakeReader(pages),
    )

    chunks = pdf_parser.parse_pdf(
        str(pdf_path),
        document_id="demo-paper",
        chunk_size=6,
        overlap=2,
    )

    assert len(chunks) == 2

    assert chunks[0].chunk_id == "demo-paper:p1:c0"
    assert chunks[0].text == "abcdef"
    assert chunks[0].page_number == 1
    assert chunks[0].char_start == 0
    assert chunks[0].char_end == 6

    assert chunks[1].chunk_id == "demo-paper:p1:c1"
    assert chunks[1].text == "efghij"
    assert chunks[1].page_number == 1
    assert chunks[1].char_start == 4
    assert chunks[1].char_end == 10


def test_parse_pdf_resets_chunk_index_for_each_page(
    monkeypatch,
    tmp_path,
) -> None:
    pdf_path = tmp_path / "demo.pdf"
    pdf_path.write_bytes(b"fake pdf content")

    pages = [
        FakePage("abcdefghij"),
        FakePage("klmnopqrst"),
    ]

    monkeypatch.setattr(
        pdf_parser,
        "PdfReader",
        lambda _: FakeReader(pages),
    )

    chunks = pdf_parser.parse_pdf(
        str(pdf_path),
        document_id="demo-paper",
        chunk_size=6,
        overlap=2,
    )

    assert [chunk.chunk_id for chunk in chunks] == [
        "demo-paper:p1:c0",
        "demo-paper:p1:c1",
        "demo-paper:p2:c0",
        "demo-paper:p2:c1",
    ]

    assert [chunk.page_number for chunk in chunks] == [1, 1, 2, 2]


def test_parse_pdf_skips_blank_pages(monkeypatch, tmp_path) -> None:
    pdf_path = tmp_path / "demo.pdf"
    pdf_path.write_bytes(b"fake pdf content")

    pages = [
        FakePage(""),
        FakePage("   "),
        FakePage("Valid page content"),
    ]

    monkeypatch.setattr(
        pdf_parser,
        "PdfReader",
        lambda _: FakeReader(pages),
    )

    chunks = pdf_parser.parse_pdf(str(pdf_path))

    assert len(chunks) == 1
    assert chunks[0].page_number == 3
    assert chunks[0].text == "Valid page content"


def test_parse_pdf_returns_empty_list_when_all_pages_are_blank(
    monkeypatch,
    tmp_path,
) -> None:
    pdf_path = tmp_path / "empty.pdf"
    pdf_path.write_bytes(b"fake pdf content")

    pages = [
        FakePage(""),
        FakePage("   "),
    ]

    monkeypatch.setattr(
        pdf_parser,
        "PdfReader",
        lambda _: FakeReader(pages),
    )

    chunks = pdf_parser.parse_pdf(str(pdf_path))

    assert chunks == []


def test_parse_pdf_raises_for_missing_file() -> None:
    with pytest.raises(IngestionError):
        pdf_parser.parse_pdf("does-not-exist.pdf")


def test_parse_pdf_raises_for_non_pdf_file(tmp_path) -> None:
    text_path = tmp_path / "document.txt"
    text_path.write_text("not a pdf", encoding="utf-8")

    with pytest.raises(IngestionError):
        pdf_parser.parse_pdf(str(text_path))


def test_parse_pdf_raises_for_invalid_chunk_parameters(
    tmp_path,
) -> None:
    pdf_path = tmp_path / "demo.pdf"
    pdf_path.write_bytes(b"fake pdf content")

    with pytest.raises(IngestionError):
        pdf_parser.parse_pdf(
            str(pdf_path),
            chunk_size=10,
            overlap=10,
        )


def test_parse_pdf_raises_for_page_extraction_failure(
    monkeypatch,
    tmp_path,
) -> None:
    pdf_path = tmp_path / "broken.pdf"
    pdf_path.write_bytes(b"fake pdf content")

    monkeypatch.setattr(
        pdf_parser,
        "PdfReader",
        lambda _: FakeReader([BrokenPage()]),
    )

    with pytest.raises(IngestionError):
        pdf_parser.parse_pdf(str(pdf_path))
