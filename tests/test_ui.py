from __future__ import annotations

import pytest
from pypdf import PdfWriter

from app.core.types import (
    AgentState,
    ClaimCheck,
    ClaimStatus,
    Evidence,
    RetrievalMethod,
    RouteDecision,
    RouteType,
)
from app.ui import (
    answer_panel_summary,
    claim_presentations,
    document_status_presentation,
    inspect_pdf_text,
    memory_write_summary,
    pdf_page_count,
    pipeline_stage_presentations,
    safe_pdf_name,
    session_navigation_items,
    source_status,
    upload_error_message,
    used_citations,
    verification_notice,
    web_search_summary,
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


def test_pdf_text_profile_marks_blank_pages_for_ocr(tmp_path) -> None:
    scanned = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_blank_page(width=100, height=100)
    with scanned.open("wb") as stream:
        writer.write(stream)

    profile = inspect_pdf_text(scanned)

    assert profile.pages == 2
    assert profile.text_pages == 0
    assert profile.scanned_pages == (1, 2)
    assert profile.needs_ocr is True


def test_upload_error_message_keeps_page_diagnostic_and_adds_ocr_fix() -> None:
    message = upload_error_message(
        RuntimeError(
            "OCR failed on page 3 of /Users/private/report.pdf: tesseract missing"
        )
    )

    assert "page 3" in message
    assert "/Users/private" not in message
    assert "brew install poppler tesseract" in message


def test_failed_reimport_keeps_old_index_visible() -> None:
    row = document_status_presentation(
        pages=1,
        attempt={
            "status": "failed",
            "stage": "OCR / embedding / 数据库索引",
            "error_type": "RetrievalError",
            "error": "Embedding service failed",
        },
        persisted={"chunks": 7, "page_count": 1},
    )

    assert row["level"] == "warning"
    assert "旧索引仍可用" in row["label"]
    assert "不代表当前上传文件已入库" in row["detail"]


def test_first_failed_import_reports_no_usable_index() -> None:
    row = document_status_presentation(
        pages=2,
        attempt={
            "status": "failed",
            "stage": "检查 PDF 文字层",
            "error_type": "PdfStreamError",
            "error": "broken file",
        },
        persisted=None,
    )

    assert row["level"] == "error"
    assert "无可用索引" in row["label"]
    assert "检查 PDF 文字层" in row["detail"]


def test_persisted_index_survives_missing_session_attempt() -> None:
    row = document_status_presentation(
        pages=4,
        attempt=None,
        persisted={
            "chunks": 12,
            "page_count": 4,
            "indexed_at": "2026-11-21T09:00:00+00:00",
        },
    )

    assert row["level"] == "success"
    assert row["label"] == "数据库索引可用 · 12 chunks · 4 页"
    assert "2026-11-21" in row["detail"]


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
            "url": None,
            "is_web": False,
        }
    ]


def test_claim_presentations_supports_clickable_web_sources() -> None:
    state = AgentState(question="latest RAG release")
    state.evidence = [
        Evidence(
            chunk_id="web-abc",
            document_id="https://example.com/rag",
            file_name="example.com",
            page_number=0,
            text="A current RAG release is available.",
            score=0.9,
            retrieval_method=RetrievalMethod.WEB,
            url="https://example.com/rag",
            title="RAG release",
        )
    ]
    state.claims = [
        ClaimCheck(
            claim="A current RAG release is available.",
            status=ClaimStatus.SUPPORTED,
            supporting_evidence=["web-abc"],
        )
    ]

    source = claim_presentations(state)[0]["sources"][0]
    assert source["is_web"] is True
    assert source["url"] == "https://example.com/rag"
    assert source["locator"] == "RAG release · 网页"


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
            "url": None,
            "title": None,
            "is_web": False,
            "locator": "rag_paper.pdf p.3",
        }
    ]


def test_web_citation_keeps_clickable_url() -> None:
    state = AgentState(question="latest")
    state.evidence = [
        Evidence(
            chunk_id="web-1",
            document_id="https://example.com/news",
            file_name="example.com",
            page_number=0,
            text="Current information.",
            score=0.9,
            retrieval_method=RetrievalMethod.WEB,
            url="https://example.com/news",
            title="Current information",
        )
    ]
    state.build_citations()
    state.final_answer = "See the current information [1]."

    citation = used_citations(state)[0]
    assert citation["is_web"] is True
    assert citation["url"] == "https://example.com/news"
    assert citation["locator"] == "Current information · 网页"


def test_web_search_summary_covers_not_triggered_success_and_failure() -> None:
    skipped = AgentState(question="q")
    skipped.log(
        "web_search", "decision", should_search=False, reason="本地证据足够"
    )
    assert web_search_summary(skipped) == (
        "info", "未触发网页搜索：本地证据足够"
    )

    searched = AgentState(question="q")
    searched.log("web_search", "decision", should_search=True)
    searched.log("web_search", "end", count=2, warning=None)
    assert web_search_summary(searched) == (
        "success", "已找到 2 条相关网页来源"
    )

    failed = AgentState(question="q")
    failed.log("web_search", "error", error="offline")
    assert web_search_summary(failed) == ("error", "网页搜索失败：offline")

    running = AgentState(question="q")
    running.log("web_search", "decision", should_search=True)
    assert web_search_summary(running) == (
        "info", "网页搜索进行中，尚未返回结果。"
    )


def test_web_search_summary_distinguishes_irrelevant_candidates_and_backend_failure() -> None:
    irrelevant = AgentState(question="q")
    irrelevant.log(
        "web_search",
        "end",
        count=0,
        warning="结果与问题无关",
        diagnostics=[
            {
                "backend": "bing",
                "kind": "irrelevant_results",
                "candidates": 4,
                "relevant_results": 0,
            }
        ],
    )
    assert web_search_summary(irrelevant) == (
        "warning",
        "网页搜索已执行，收到 4 条候选，但没有找到与问题相关的可用来源。",
    )

    failed = AgentState(question="q")
    failed.log(
        "web_search",
        "end",
        count=0,
        warning="Tavily 搜索失败: ConnectTimeout",
        diagnostics=[{"backend": "tavily", "kind": "ConnectTimeout"}],
    )
    assert web_search_summary(failed) == (
        "error", "网页搜索失败：Tavily 搜索失败: ConnectTimeout"
    )


def test_session_navigation_items_preserve_order_summary_and_selection() -> None:
    rows = session_navigation_items(
        [
            {
                "session_id": "recent",
                "turn_count": 2,
                "last_at": "2026-11-22T08:30:00+00:00",
                "last_question": "Summarise the RAG paper",
            },
            {
                "session_id": "empty",
                "turn_count": 0,
                "last_at": "2026-11-21T10:00:00+00:00",
                "last_question": "",
            },
        ],
        "recent",
    )

    assert [row["session_id"] for row in rows] == ["recent", "empty"]
    assert rows[0]["selected"] is True
    assert rows[0]["summary"] == "Summarise the RAG paper"
    assert rows[0]["timestamp"] == "2026-11-22 08:30"
    assert rows[1]["summary"] == "新会话"


def test_answer_panel_summary_counts_visible_attention_states() -> None:
    state = AgentState(question="q", errors=["retrieval: offline"])
    state.claims = [
        ClaimCheck(claim="supported", status=ClaimStatus.SUPPORTED),
        ClaimCheck(claim="bad", status=ClaimStatus.UNSUPPORTED),
        ClaimCheck(
            claim="unknown",
            status=ClaimStatus.UNSUPPORTED,
            note="UNVERIFIED: ambiguous",
        ),
    ]

    assert answer_panel_summary(state) == {
        "citations": 0,
        "claims": 3,
        "unsupported": 1,
        "unverified": 1,
        "errors": 1,
    }


def test_source_status_distinguishes_web_document_direct_and_failure() -> None:
    direct = AgentState(question="q")
    direct.route = RouteDecision(route=RouteType.DIRECT)
    assert source_status(direct)[0] == "direct"

    document = AgentState(question="q")
    document.evidence = [
        Evidence(
            chunk_id="d1", document_id="d", file_name="f.pdf",
            page_number=1, text="doc", score=1.0,
        )
    ]
    assert source_status(document)[0] == "document"

    web = AgentState(question="q")
    web.evidence = [
        Evidence(
            chunk_id="w1", document_id="https://example.com",
            file_name="example.com", page_number=0, text="web", score=1.0,
            retrieval_method=RetrievalMethod.WEB,
            url="https://example.com",
        )
    ]
    assert source_status(web)[0] == "web"

    failed = AgentState(question="q", errors=["retrieval: offline"])
    assert source_status(failed)[0] == "retrieval_failed"


def test_source_status_distinguishes_document_library_states() -> None:
    state = AgentState(question="q")
    state.route = RouteDecision(
        route=RouteType.DOCUMENT_RETRIEVAL, need_retrieval=True
    )

    assert source_status(
        state, uploaded_documents=0, indexed_documents=0
    )[0] == "no_documents"
    assert source_status(
        state, uploaded_documents=1, indexed_documents=0
    )[0] == "not_indexed"
    assert source_status(
        state, uploaded_documents=2, indexed_documents=1
    )[0] == "indexed_no_evidence"
    assert source_status(
        state, uploaded_documents=1, indexed_documents=None
    )[0] == "index_unknown"


def test_pipeline_stage_presentations_reports_latency_counts_and_failure() -> None:
    state = AgentState(question="q")
    state.log("retrieval", "end", evidence=3, degraded=True)
    state.log("retrieval", "timing", 125.4)
    state.log("web_search", "error", error="offline")
    state.log("verify", "end", claims=2, unsupported=1)
    state.log("verify", "timing", 20.0)

    rows = {row["stage"]: row for row in pipeline_stage_presentations(state)}

    assert rows["retrieval"] == {
        "stage": "retrieval",
        "label": "文档检索",
        "status": "完成",
        "latency_ms": 125.4,
        "detail": "证据 3 条（已降级）",
    }
    assert rows["web_search"]["status"] == "失败"
    assert rows["verify"]["detail"] == "claims 2 条"


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


def test_memory_summary_reports_contradiction_updates_and_update_failures() -> None:
    updated = AgentState(question="Change my preference", enable_memory=True)
    updated.log(
        "memory_write",
        "stats",
        skipped=None,
        proposed=1,
        accepted=1,
        contradictions_checked=1,
        contradictions_updated=1,
    )
    updated.log("memory_write", "end", stored=0, updated=1)
    assert memory_write_summary(updated) == (
        "success", "本轮已更新 1 条冲突的长期记忆。"
    )

    mixed = AgentState(question="Remember two things", enable_memory=True)
    mixed.log("memory_write", "stats", skipped=None, proposed=2, accepted=2)
    mixed.log("memory_write", "end", stored=1, updated=1)
    assert memory_write_summary(mixed) == (
        "success", "本轮新增 1 条长期记忆，并更新 1 条冲突记忆。"
    )

    failed = AgentState(question="Change my preference", enable_memory=True)
    failed.log("memory_write", "update_error", error="RuntimeError")
    assert memory_write_summary(failed) == (
        "error", "长期记忆更新失败：RuntimeError"
    )
