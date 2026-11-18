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
from app.ui import (
    claim_presentations,
    memory_write_summary,
    pdf_page_count,
    safe_pdf_name,
    used_citations,
    verification_notice,
)


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


def test_t07_insufficient_answer_hides_unrelated_candidate_citations() -> None:
    state = AgentState(question="HKU 2026 QS?", enable_verification=True)
    state.route = RouteDecision(
        route=RouteType.DOCUMENT_RETRIEVAL, need_retrieval=True
    )
    state.evidence = [
        Evidence(
            chunk_id="rag:p2:c0",
            document_id="rag",
            file_name="rag_paper.pdf",
            page_number=2,
            text="RAG model architecture.",
            score=0.01,
        )
    ]
    state.build_citations()
    state.final_answer = "现有资料不足以回答 HKU 2026 年 QS 排名。"

    assert used_citations(state) == []
    assert verification_notice(state) == (
        "warning",
        "在已上传材料中未找到支持本问题的文档证据。",
    )


def test_normal_answer_displays_only_explicitly_used_citations() -> None:
    state = AgentState(question="Explain RAG")
    state.evidence = [
        Evidence(
            chunk_id="rag:p2:c0",
            document_id="rag",
            file_name="rag_paper.pdf",
            page_number=2,
            text="Retriever details.",
            score=1.0,
        ),
        Evidence(
            chunk_id="rag:p3:c0",
            document_id="rag",
            file_name="rag_paper.pdf",
            page_number=3,
            text="Generator details.",
            score=0.9,
        ),
    ]
    state.build_citations()
    state.final_answer = "Generator details are described here [2]."

    assert used_citations(state) == [
        {
            "index": 2,
            "file_name": "rag_paper.pdf",
            "page_number": 3,
            "quote": "Generator details.",
        }
    ]


def test_verification_warning_does_not_hide_valid_draft_citations() -> None:
    state = AgentState(question="Explain RAG")
    state.evidence = [
        Evidence(
            chunk_id="rag:p2:c0",
            document_id="rag",
            file_name="rag_paper.pdf",
            page_number=2,
            text="Retriever details.",
            score=1.0,
        )
    ]
    state.build_citations()
    state.draft_answer = "RAG uses a retriever [1]."
    state.final_answer = (
        state.draft_answer
        + "\n\nℹ️ 以下内容现有证据不足，校验器未能可靠确认。"
    )

    assert [item["index"] for item in used_citations(state)] == [1]


def test_memory_summary_distinguishes_no_signal_from_failure() -> None:
    state = AgentState(question="Explain RAG", enable_memory=True)
    state.log(
        "memory_write",
        "stats",
        skipped="no_signal",
        proposed=0,
        accepted=0,
    )

    assert memory_write_summary(state) == (
        "info",
        "本轮没有检测到需要长期保存的稳定信息。",
    )

    failed = AgentState(question="Remember this", enable_memory=True)
    failed.log("memory_write", "error", error="database unavailable")
    assert memory_write_summary(failed) == (
        "error",
        "长期记忆写入失败：database unavailable",
    )


def test_memory_summary_reports_confirmed_store_and_filtered_candidates() -> None:
    stored = AgentState(question="Remember my preference", enable_memory=True)
    stored.log(
        "memory_write",
        "stats",
        skipped=None,
        proposed=1,
        accepted=1,
    )
    stored.log("memory_write", "end", stored=1)
    assert memory_write_summary(stored) == (
        "success",
        "本轮已保存 1 条长期记忆。",
    )

    filtered = AgentState(question="Remember duplicate", enable_memory=True)
    filtered.log(
        "memory_write",
        "stats",
        skipped=None,
        proposed=1,
        accepted=0,
        dropped_duplicate=1,
        dropped_sensitive=0,
        dropped_invalid=0,
    )
    filtered.log("memory_write", "end", stored=0)
    assert memory_write_summary(filtered) == (
        "info",
        "模型建议 1 条记忆，但未保存：重复 1 条。",
    )
