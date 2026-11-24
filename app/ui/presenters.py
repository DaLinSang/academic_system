"""Pure presentation helpers for the Streamlit UI.

Keeping these transformations independent from Streamlit makes the status,
evidence, and upload behavior easy to test without starting a web server.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from app.core.types import AgentState, ClaimStatus
from app.verification import is_insufficient_answer

_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+")
_CITATION_INDEX = re.compile(r"\[(\d+)\]")
_ABSOLUTE_PATH = re.compile(r"(?<![A-Za-z0-9_])(?:/[^\s:]+)+")

_STATUS_VIEW = {
    ClaimStatus.SUPPORTED: ("supported", "success", "有证据支持"),
    ClaimStatus.PARTIALLY_SUPPORTED: ("partially_supported", "warning", "部分支持"),
    ClaimStatus.UNSUPPORTED: ("unsupported", "error", "证据不支持"),
    ClaimStatus.UNVERIFIED: ("unverified", "warning", "未能确认"),
}


@dataclass(frozen=True)
class PdfTextProfile:
    """Cheap pre-index inspection used only to explain OCR work in the UI."""

    pages: int
    text_pages: int
    scanned_pages: tuple[int, ...]

    @property
    def needs_ocr(self) -> bool:
        return bool(self.scanned_pages)


def inspect_pdf_text(path: Path) -> PdfTextProfile:
    """Identify pages without a text layer before the blocking index call.

    The ingestion module remains the source of truth and performs the actual OCR.
    This helper only gives the user an honest progress label.
    """
    reader = PdfReader(path)
    scanned: list[int] = []
    text_pages = 0
    for page_number, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").replace("\x00", "").strip()
        if text:
            text_pages += 1
        else:
            scanned.append(page_number)
    return PdfTextProfile(
        pages=len(reader.pages),
        text_pages=text_pages,
        scanned_pages=tuple(scanned),
    )


def upload_error_message(exc: Exception) -> str:
    """Return actionable, bounded upload diagnostics without leaking local paths."""
    detail = str(exc).strip() or type(exc).__name__
    detail = _ABSOLUTE_PATH.sub("<本地文件>", detail)[:500]
    lowered = detail.casefold()
    if any(
        marker in lowered
        for marker in ("ocr", "tesseract", "pdftoppm", "searchable text")
    ):
        return (
            f"{detail} 安装后可重试：macOS 执行 `brew install poppler tesseract`，"
            "并用 `which pdftoppm tesseract` 检查命令是否可用；中文扫描件还需配置 "
            "`PDF_OCR_LANG=eng+chi_sim`。"
        )
    return detail


def document_status_presentation(
    *,
    pages: int | None,
    attempt: dict[str, Any] | None,
    persisted: dict[str, Any] | None,
) -> dict[str, str]:
    """Merge the latest import attempt with the durable database state."""
    attempt = attempt or {}
    persisted = persisted or {}
    page_text = f"{pages} 页" if pages is not None else "页数不可读"
    chunks = int(persisted.get("chunks", 0) or 0)
    db_pages = int(persisted.get("page_count", 0) or 0)
    status = str(attempt.get("status", "unknown"))

    if status == "failed":
        error_type = str(attempt.get("error_type") or "导入错误")
        stage = str(attempt.get("stage") or "未知阶段")
        error = str(attempt.get("error") or "未记录详细原因")
        if chunks:
            return {
                "level": "warning",
                "label": (
                    f"本次重导入失败；旧索引仍可用 · {chunks} chunks"
                    f" · {db_pages} 页"
                ),
                "detail": (
                    f"失败阶段：{stage} · {error_type}: {error}。"
                    "数据库内容来自此前成功导入，不代表当前上传文件已入库。"
                ),
            }
        return {
            "level": "error",
            "label": "本次导入失败；数据库中无可用索引",
            "detail": f"失败阶段：{stage} · {error_type}: {error}",
        }

    if chunks:
        indexed_at = str(persisted.get("indexed_at") or "").strip()
        suffix = f" · 索引记录 {indexed_at}" if indexed_at else ""
        return {
            "level": "success",
            "label": f"数据库索引可用 · {chunks} chunks · {db_pages} 页",
            "detail": f"本地文件：{page_text}{suffix}",
        }
    if status == "saved":
        return {
            "level": "info",
            "label": "文件已保存 · 尚未建立索引",
            "detail": f"本地文件：{page_text}",
        }
    return {
        "level": "info",
        "label": "文件存在 · 数据库中无可用索引",
        "detail": f"本地文件：{page_text}",
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
            "url": by_index[index].url,
            "title": by_index[index].title,
            "is_web": by_index[index].is_web,
            "locator": by_index[index].locator,
        }
        for index in indices
        if index in by_index
    ]


def web_search_summary(state: AgentState) -> tuple[str, str]:
    """Summarize the web-search decision and execution for the run panel."""
    events = [event for event in state.trace if event.stage == "web_search"]
    errors = [event for event in events if event.event == "error"]
    if errors:
        detail = str(errors[-1].detail.get("error", "未知错误"))
        return ("error", f"网页搜索失败：{detail}")

    ends = [event for event in events if event.event == "end"]
    if ends:
        detail = ends[-1].detail
        count = int(detail.get("count", 0) or 0)
        warning = str(detail.get("warning") or "").strip()
        diagnostics = [
            row for row in (detail.get("diagnostics") or []) if isinstance(row, dict)
        ]
        if count:
            suffix = f"；{warning}" if warning else ""
            return ("success", f"已找到 {count} 条相关网页来源{suffix}")
        irrelevant = [
            row for row in diagnostics if row.get("kind") == "irrelevant_results"
        ]
        if irrelevant:
            candidates = sum(int(row.get("candidates", 0) or 0) for row in irrelevant)
            candidate_text = f"收到 {candidates} 条候选，" if candidates else ""
            return (
                "warning",
                f"网页搜索已执行，{candidate_text}但没有找到与问题相关的可用来源。",
            )
        benign_kinds = {"ok", "zero_results", "irrelevant_results"}
        failures = [
            row for row in diagnostics if str(row.get("kind") or "") not in benign_kinds
        ]
        if failures:
            return ("error", f"网页搜索失败：{warning or '搜索后端未能返回结果'}")
        if warning:
            return ("warning", f"网页搜索未取得证据：{warning}")
        return ("warning", "网页搜索已执行，但没有取得证据。")

    decisions = [event for event in events if event.event == "decision"]
    if decisions:
        detail = decisions[-1].detail
        if bool(detail.get("should_search")):
            return ("info", "网页搜索进行中，尚未返回结果。")
        reason = str(detail.get("reason") or "本轮无需联网")
        return ("info", f"未触发网页搜索：{reason}")
    return ("info", "未触发网页搜索。")


def session_navigation_items(
    sessions: list[dict[str, Any]], current_session_id: str
) -> list[dict[str, Any]]:
    """Build compact, scan-friendly session rows for the left rail."""
    rows: list[dict[str, Any]] = []
    for item in sessions:
        session_id = str(item.get("session_id") or "")
        question = str(item.get("last_question") or "").strip()
        if question:
            summary = question
        elif session_id.startswith("chat-"):
            summary = "新会话"
        else:
            summary = session_id
        last_at = str(item.get("last_at") or "")
        timestamp = last_at[:16].replace("T", " ") if last_at else ""
        rows.append(
            {
                "session_id": session_id,
                "summary": summary,
                "timestamp": timestamp,
                "turn_count": int(item.get("turn_count", 0) or 0),
                "selected": session_id == current_session_id,
            }
        )
    return rows


def answer_panel_summary(state: AgentState) -> dict[str, int]:
    """Return counts used in collapsed answer-detail panel titles."""
    citations = used_citations(state)
    unsupported = sum(
        check.status == ClaimStatus.UNSUPPORTED
        and not (check.note or "").startswith("UNVERIFIED:")
        for check in state.claims
    )
    unverified = sum(
        check.status == ClaimStatus.UNVERIFIED
        or (check.note or "").startswith("UNVERIFIED:")
        for check in state.claims
    )
    return {
        "citations": len(citations),
        "claims": len(state.claims),
        "unsupported": unsupported,
        "unverified": unverified,
        "errors": len(state.errors),
    }


def source_status(
    state: AgentState,
    *,
    uploaded_documents: int | None = None,
    indexed_documents: int | None = None,
) -> tuple[str, str, str]:
    """Return a stable source-status key, UI level, and human-readable label."""
    if any(item.is_web for item in state.evidence):
        return ("web", "success", "本轮使用了网页证据。")
    if state.evidence:
        return ("document", "success", "本轮使用了上传文档证据。")

    retrieval_events = [
        event
        for event in state.trace
        if event.stage in {"retrieval", "web_search"}
    ]
    if state.errors or any(event.event == "error" for event in retrieval_events):
        return ("retrieval_failed", "error", "本轮检索失败，未取得可用证据。")
    if state.route and state.route.is_direct:
        return ("direct", "info", "本轮为 direct 回答，未使用外部证据。")
    if uploaded_documents == 0:
        return (
            "no_documents",
            "warning",
            "尚未上传 PDF；本轮没有可检索的用户文档。",
        )
    if uploaded_documents and indexed_documents == 0:
        return (
            "not_indexed",
            "warning",
            "已上传 PDF，但尚未成功建立索引；请在上传区重试导入。",
        )
    if uploaded_documents and indexed_documents is None:
        return (
            "index_unknown",
            "warning",
            "已有上传文档，但本次启动无法确认索引状态；若检索不到可重新导入。",
        )
    if indexed_documents:
        return (
            "indexed_no_evidence",
            "warning",
            "已有已索引文档，但本轮没有检索到相关证据。",
        )
    return ("no_evidence", "warning", "本轮执行了检索，但没有找到可用证据。")


def pipeline_stage_presentations(state: AgentState) -> list[dict[str, Any]]:
    """Summarize observable pipeline events without exposing model reasoning."""
    labels = {
        "memory_read": "读取长期记忆",
        "router": "路由决策",
        "plan": "任务规划",
        "retrieval": "文档检索",
        "web_search": "网页搜索",
        "generate": "生成回答",
        "verify": "Claim 校验",
        "replan": "补充规划",
        "memory_write": "保存长期记忆",
    }
    rows: list[dict[str, Any]] = []
    for stage, label in labels.items():
        events = [event for event in state.trace if event.stage == stage]
        if not events:
            continue
        timing = sum(
            event.latency_ms for event in events if event.event == "timing"
        )
        if any(event.event == "error" or event.event.endswith("_error") for event in events):
            status = "失败"
        elif events[-1].event == "skip":
            status = "跳过"
        elif any(event.event in {"end", "timing"} for event in events):
            status = "完成"
        else:
            status = "已记录"

        detail = ""
        end_events = [event for event in events if event.event == "end"]
        if end_events:
            end = end_events[-1].detail
            if stage == "retrieval":
                detail = f"证据 {int(end.get('evidence', 0) or 0)} 条"
                if end.get("degraded"):
                    detail += "（已降级）"
            elif stage == "web_search":
                detail = f"网页证据 {int(end.get('count', 0) or 0)} 条"
            elif stage == "verify":
                detail = f"claims {int(end.get('claims', 0) or 0)} 条"
            elif stage == "memory_read":
                detail = f"命中 {int(end.get('hits', 0) or 0)} 条"
            elif stage == "memory_write":
                stored = int(end.get("stored", 0) or 0)
                updated = int(end.get("updated", 0) or 0)
                parts = [f"新增 {stored} 条"]
                if updated:
                    parts.append(f"更新 {updated} 条")
                detail = "，".join(parts)
        rows.append(
            {
                "stage": stage,
                "label": label,
                "status": status,
                "latency_ms": round(timing, 2),
                "detail": detail,
            }
        )
    return rows


def memory_write_summary(state: AgentState) -> tuple[str, str]:
    """Summarize the memory-write funnel without claiming an unconfirmed save."""
    if not state.enable_memory:
        return ("info", "本轮长期记忆已关闭。")

    events = [event for event in state.trace if event.stage == "memory_write"]
    errors = [
        event
        for event in events
        if event.event == "error" or event.event.endswith("_error")
    ]
    if errors:
        detail = str(errors[-1].detail.get("error", "未知错误"))
        action = "更新" if errors[-1].event == "update_error" else "写入"
        return ("error", f"长期记忆{action}失败：{detail}")

    stats_events = [event for event in events if event.event == "stats"]
    end_events = [event for event in events if event.event == "end"]
    if not stats_events:
        return ("info", "本轮没有可用的记忆写入决策记录。")

    stats = stats_events[-1].detail
    skipped = stats.get("skipped")
    if skipped == "no_signal":
        return ("info", "本轮没有检测到需要长期保存的稳定信息。")
    if skipped:
        return ("warning", f"本轮未生成可保存的记忆候选：{skipped}")

    proposed = int(stats.get("proposed", 0) or 0)
    accepted = int(stats.get("accepted", 0) or 0)
    stored = int(end_events[-1].detail.get("stored", 0) or 0) if end_events else 0
    updated = int(end_events[-1].detail.get("updated", 0) or 0) if end_events else 0
    if stored and updated:
        return (
            "success",
            f"本轮新增 {stored} 条长期记忆，并更新 {updated} 条冲突记忆。",
        )
    if updated:
        return ("success", f"本轮已更新 {updated} 条冲突的长期记忆。")
    if stored:
        return ("success", f"本轮已保存 {stored} 条长期记忆。")
    if accepted and not end_events:
        return ("warning", f"已接受 {accepted} 条记忆候选，但没有写入完成记录。")
    if proposed:
        reasons = []
        for key, label in (
            ("dropped_duplicate", "重复"),
            ("dropped_sensitive", "敏感信息"),
            ("dropped_invalid", "格式无效"),
        ):
            count = int(stats.get(key, 0) or 0)
            if count:
                reasons.append(f"{label} {count} 条")
        suffix = "；".join(reasons) or "未通过保存规则"
        return ("info", f"模型建议 {proposed} 条记忆，但未保存：{suffix}。")
    return ("info", "本轮模型没有建议保存长期记忆。")


def safe_pdf_name(filename: str) -> str:
    """Return a path-safe PDF name and reject non-PDF uploads."""
    basename = Path(filename).name.strip()
    if Path(basename).suffix.lower() != ".pdf":
        raise ValueError("Only PDF files are supported.")
    stem = _UNSAFE_FILENAME.sub("_", Path(basename).stem).strip("._")
    return f"{stem or 'document'}.pdf"


def delete_document_assets(
    retriever: Any,
    file_name: str,
    raw_dir: Path,
) -> tuple[bool, bool]:
    """Delete a document index and its owned upload, returning both outcomes."""
    safe_name = safe_pdf_name(file_name)
    delete_document = getattr(retriever, "delete_document", None)
    if not callable(delete_document):
        raise RuntimeError("当前 Retriever 不支持删除文档。")
    index_deleted = bool(delete_document(safe_name))
    path = raw_dir / safe_name
    file_deleted = path.exists()
    if file_deleted:
        path.unlink()
    return index_deleted, file_deleted


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
        if check.status == ClaimStatus.UNVERIFIED or note.startswith("UNVERIFIED:"):
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
                "url": evidence[chunk_id].url,
                "is_web": evidence[chunk_id].is_web,
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
