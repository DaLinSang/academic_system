from __future__ import annotations

import pytest
from pypdf import PdfWriter

from app.core.types import (
    AgentState,
    ClaimCheck,
    ClaimStatus,
    Evidence,
    RouteDecision,
    RouteType,
)
from app.ui import claim_presentations, pdf_page_count, safe_pdf_name, verification_notice


def test_safe_pdf_name_removes_paths_and_unsafe_characters() -> None:
    assert safe_pdf_name("../../My paper (final).PDF") == "My_paper_final.pdf"
    assert safe_pdf_name("课程 讲义.pdf") == "课程_讲义.pdf"


def test_safe_pdf_name_rejects_non_pdf() -> None:
    with pytest.raises(ValueError):
        safe_pdf_name("notes.txt")


def test_pdf_page_count_handles_valid_and_invalid_files(tmp_path) -> None:
    valid = tmp_path / "two-pages.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_blank_page(width=100, height=100)
    with valid.open("wb") as stream:
        writer.write(stream)

    invalid = tmp_path / "broken.pdf"
    invalid.write_text("not a pdf", encoding="utf-8")

    assert pdf_page_count(valid) == 2
    assert pdf_page_count(invalid) is None


@pytest.mark.parametrize(
    ("status", "level", "label"),
    [
        (ClaimStatus.SUPPORTED, "success", "有证据支持"),
        (ClaimStatus.PARTIALLY_SUPPORTED, "warning", "部分支持"),
        (ClaimStatus.UNSUPPORTED, "error", "证据不支持"),
    ],
)
def test_claim_presentations_maps_status_colors(status, level, label) -> None:
    state = AgentState(question="q")
    state.claims = [ClaimCheck(claim="claim", status=status, confidence=0.8)]

    row = claim_presentations(state)[0]

    assert row["level"] == level
    assert row["label"] == label


def test_claim_presentations_resolves_real_evidence_locator() -> None:
    state = AgentState(question="q")
    state.evidence = [
        Evidence(
            chunk_id="lec04:p9:c1",
            document_id="lec04",
            file_name="Lec04.pdf",
            page_number=9,
            text="Self-RAG uses reflection tokens.",
            score=1.0,
        )
    ]
    state.claims = [
        ClaimCheck(
            claim="Self-RAG uses reflection tokens.",
            status=ClaimStatus.SUPPORTED,
            confidence=1.0,
            supporting_evidence=["lec04:p9:c1", "missing"],
        )
    ]

    row = claim_presentations(state)[0]

    assert row["sources"] == [
        {
            "chunk_id": "lec04:p9:c1",
            "file_name": "Lec04.pdf",
            "page_number": 9,
            "locator": "Lec04.pdf p.9",
        }
    ]


def test_unverified_claim_uses_neutral_warning_copy() -> None:
    state = AgentState(question="q")
    state.claims = [
        ClaimCheck(
            claim="ambiguous claim",
            status=ClaimStatus.UNSUPPORTED,
            confidence=0,
            note="UNVERIFIED: conflicting evidence",
        )
    ]

    row = claim_presentations(state)[0]

    assert row["level"] == "warning"
    assert row["label"] == "未能确认"
    assert row["note"] == "conflicting evidence"
    assert "非事实正确概率" in row["score_label"]


def test_direct_without_evidence_has_unverified_notice() -> None:
    state = AgentState(question="q", enable_verification=True)
    state.route = RouteDecision(route=RouteType.DIRECT)

    assert verification_notice(state) == (
        "info",
        "本轮未进行文档证据校验；回答基于模型通用知识。",
    )


def test_retrieval_without_evidence_has_missing_evidence_notice() -> None:
    state = AgentState(question="q", enable_verification=True)
    state.route = RouteDecision(
        route=RouteType.DOCUMENT_RETRIEVAL, need_retrieval=True
    )

    assert verification_notice(state) == (
        "warning",
        "本轮执行了文档检索，但没有找到可用于校验的相关证据。",
    )
