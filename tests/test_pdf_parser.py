"""PDF ingestion parser tests."""

from __future__ import annotations

import pytest

from app.core.errors import IngestionError
from app.ingestion import pdf_parser


class FakePage:
    """用于测试 PDF 页面文本提取。"""

    def __init__(self, text: str) -> None:
        self.text = text

    def extract_text(self) -> str:
        """返回模拟页面文本。"""
        return self.text


class BrokenPage:
    """用于测试页面解析失败。"""

    def extract_text(self) -> str:
        """模拟页面解析异常。"""
        raise RuntimeError("page extraction failed")


class FakeReader:
    """用于替代 pypdf.PdfReader。"""

    def __init__(self, pages: list[object]) -> None:
        self.pages = pages


def test_parse_pdf_preserves_page_numbers(monkeypatch, tmp_path)->None:
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


def test_parse_pdf_skips_blank_pages(monkeypatch, tmp_path)->None:
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
)->None:
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


def test_parse_pdf_raises_for_missing_file()->None:
    with pytest.raises(IngestionError):
        pdf_parser.parse_pdf("does-not-exist.pdf")


def test_parse_pdf_raises_for_non_pdf_file(tmp_path)->None:
    text_path = tmp_path / "document.txt"
    text_path.write_text("not a pdf", encoding="utf-8")

    with pytest.raises(IngestionError):
        pdf_parser.parse_pdf(str(text_path))


def test_parse_pdf_raises_for_page_extraction_failure(
    monkeypatch,
    tmp_path,
)->None:
    pdf_path = tmp_path / "broken.pdf"
    pdf_path.write_bytes(b"fake pdf content")

    monkeypatch.setattr(
        pdf_parser,
        "PdfReader",
        lambda _: FakeReader([BrokenPage()]),
    )

    with pytest.raises(IngestionError):
        pdf_parser.parse_pdf(str(pdf_path))
