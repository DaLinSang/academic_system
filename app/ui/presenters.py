"""Pure presentation helpers for the Streamlit UI.

Keeping these transformations independent from Streamlit makes the status,
evidence, and upload behavior easy to test without starting a web server.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from app.core.types import AgentState, ClaimStatus
from app.verification import is_insufficient_answer

_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+")
_CITATION_INDEX = re.compile(r"\[(\d+)\]")

_STATUS_VIEW = {
    ClaimStatus.SUPPORTED: ("supported", "success", "有证据支持"),
    ClaimStatus.PARTIALLY_SUPPORTED: ("partially_supported", "warning", "部分支持"),
    ClaimStatus.UNSUPPORTED: ("unsupported", "error", "证据不支持"),
}


def verification_notice(state: AgentState) -> tuple[str, str] | None:
    """Explain why no claim result exists without implying factual error."""
    if state.claims or not state.enable_verification:
        return None
    answer = state.draft_answer or state.final_answer
    if is_insufficient_answer(answer):
        return ("warning", "在已上传材料中未找到支持本问题的文档证据。")
    if state.evidence:
        return ("info", "本次回答没有可独立校验的事实陈述。")
    if state.route and state.route.is_direct:
        return ("info", "本轮未进行文档证据校验；回答基于模型通用知识。")
    return ("warning", "本轮执行了文档检索，但没有找到可用于校验的相关证据。")


def used_citations(state: AgentState) -> list[dict[str, Any]]:
    """Return only citations explicitly used by a non-refusal answer."""
    answer = state.draft_answer or state.final_answer
    if not answer or is_insufficient_answer(answer):
        return []
    indices: list[int] = []
    for raw in _CITATION_INDEX.findall(answer):
        index = int(raw)
        if index not in indices:
            indices.append(index)
    by_index = {citation.index: citation for citation in state.citations}
    return [
        {
            "index": by_index[index].index,
            "file_name": by_index[index].file_name,
            "page_number": by_index[index].page_number,
            "quote": by_index[index].quote,
        }
        for index in indices
        if index in by_index
    ]


def safe_pdf_name(filename: str) -> str:
    """Return a path-safe PDF name and reject non-PDF uploads."""
    basename = Path(filename).name.strip()
    if Path(basename).suffix.lower() != ".pdf":
        raise ValueError("Only PDF files are supported.")
    stem = _UNSAFE_FILENAME.sub("_", Path(basename).stem).strip("._")
    return f"{stem or 'document'}.pdf"


def pdf_page_count(path: Path) -> int | None:
    """Read a PDF page count, returning None for an unreadable document."""
    try:
        return len(PdfReader(path).pages)
    except Exception:  # noqa: BLE001 - a bad upload should not break the whole UI
        return None


def claim_presentations(state: AgentState) -> list[dict[str, Any]]:
    """Convert ClaimCheck objects into UI-ready rows with real locators."""
    evidence = state.evidence_by_chunk_id()
    rows: list[dict[str, Any]] = []
    for check in state.claims:
        status, level, label = _STATUS_VIEW[check.status]
        note = check.note or ""
        if note.startswith("UNVERIFIED:"):
            level, label = "warning", "未能确认"
            note = note.removeprefix("UNVERIFIED:").strip()
        lexical = "LEXICAL_SCORE:" in note or "lexical fallback" in note
        note = note.replace("LEXICAL_SCORE:", "").strip()
        sources = [
            {
                "chunk_id": chunk_id,
                "file_name": evidence[chunk_id].file_name,
                "page_number": evidence[chunk_id].page_number,
                "locator": evidence[chunk_id].locator,
            }
            for chunk_id in check.supporting_evidence
            if chunk_id in evidence
        ]
        rows.append(
            {
                "claim": check.claim,
                "status": status,
                "level": level,
                "label": label,
                "confidence": check.confidence,
                "score_label": (
                    "词面支持得分（非事实正确概率）"
                    if lexical
                    else "校验判断置信度（非事实正确概率）"
                ),
                "note": note,
                "sources": sources,
            }
        )
    return rows
