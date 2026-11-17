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

_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+")

_STATUS_VIEW = {
    ClaimStatus.SUPPORTED: ("supported", "success", "有证据支持"),
    ClaimStatus.PARTIALLY_SUPPORTED: ("partially_supported", "warning", "部分支持"),
    ClaimStatus.UNSUPPORTED: ("unsupported", "error", "缺少证据"),
}


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
                "note": check.note or "",
                "sources": sources,
            }
        )
    return rows
