"""Member C's Streamlit UI for the academic assistant."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import streamlit as st

from app.agent.factory import build_mock_orchestrator, build_stage1_orchestrator
from app.agent.orchestrator import MODE_PRESETS
from app.agent.session_store import SessionStore
from app.core.config import settings
from app.core.types import AgentState, ConversationTurn
from app.ui.presenters import (
    answer_panel_summary,
    claim_presentations,
    delete_document_assets,
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

_APP_CSS = """
<style>
:root {
  --aa-bg: light-dark(#f7f8fa, #0f1216);
  --aa-panel: light-dark(#ffffff, #181c22);
  --aa-sidebar: light-dark(#f1f3f6, #14181e);
  --aa-border: light-dark(#dfe3e8, #303640);
  --aa-border-strong: light-dark(#c2c9d2, #4a5360);
  --aa-text: light-dark(#20262e, #e8ebef);
  --aa-text-soft: light-dark(#68717d, #a5aeba);
  --aa-accent: light-dark(#304a67, #82a9d0);
  --aa-accent-soft: light-dark(#e6edf4, #25374a);
  --aa-success: light-dark(#3e7b5b, #71b493);
  --aa-input: light-dark(#ffffff, #1b2027);
  --aa-rail: light-dark(rgba(255,255,255,.62), rgba(20,24,30,.82));
  --aa-shadow: light-dark(rgba(29,42,57,.10), rgba(0,0,0,.30));
}
[data-testid="stAppViewContainer"] {
  background: var(--aa-bg);
  color: var(--aa-text);
  font-size: 15px;
}
[data-testid="stHeader"] { background: transparent; height: 2.25rem; }
[data-testid="stToolbar"] { background: transparent; }
[data-testid="stSidebar"] {
  background: var(--aa-sidebar);
  border-right: 1px solid var(--aa-border);
  color: var(--aa-text);
}
[data-testid="stAppViewContainer"] p,
[data-testid="stAppViewContainer"] label,
[data-testid="stAppViewContainer"] h1,
[data-testid="stAppViewContainer"] h2,
[data-testid="stAppViewContainer"] h3,
[data-testid="stSidebar"] p,
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] h1,
[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3 { color: var(--aa-text); }
[data-testid="stSidebar"] .block-container {
  padding-top: 1.1rem;
  padding-left: .85rem;
  padding-right: .85rem;
}
.block-container {
  max-width: 1460px;
  padding-top: .75rem;
  padding-bottom: 6.5rem;
  padding-left: 2rem;
  padding-right: 2rem;
}
h1, h2, h3 { letter-spacing: -0.025em; }
.aa-hero { padding: .35rem 0 1.35rem; border-bottom: 1px solid var(--aa-border); margin-bottom: 1.2rem; }
.aa-hero-kicker {
  color: var(--aa-accent);
  font-size: .72rem;
  font-weight: 700;
  letter-spacing: .12em;
  text-transform: uppercase;
  margin-bottom: .38rem;
}
.aa-hero h1 {
  color: var(--aa-text);
  font-size: 2rem;
  line-height: 1.16;
  font-weight: 650;
  margin: 0 0 .42rem;
}
.aa-hero p { color: var(--aa-text-soft) !important; font-size: .9rem; margin: 0; }
[data-testid="stChatMessage"] {
  background: transparent;
  border: 0;
  border-bottom: 1px solid var(--aa-border);
  border-radius: 0;
  padding: .65rem .2rem 1rem;
  margin-bottom: .35rem;
}
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] p {
  line-height: 1.68;
  font-size: .96rem;
  max-width: 82ch;
}
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] {
  width: 100%;
  max-width: 82ch;
  font-size: .96rem;
  line-height: 1.62;
}
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] :is(p, li, td, th, blockquote) {
  font-size: .96rem !important;
  line-height: 1.62 !important;
}
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] h1 { font-size: 1.22rem !important; }
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] h2 { font-size: 1.12rem !important; }
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] h3,
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] h4 { font-size: 1.03rem !important; }
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] table {
  display: block;
  width: 100%;
  max-width: 100%;
  overflow-x: auto;
  border-collapse: collapse;
}
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] :is(div, section, article) {
  max-width: 100%;
}
.aa-claim-divider {
  border-top: 1px dashed var(--aa-border-strong);
  margin: 1rem 0 .75rem;
}
.aa-claim-card {
  border-left: 3px solid var(--aa-accent);
  background: var(--aa-accent-soft);
  border-radius: 6px;
  padding: .55rem .7rem;
  margin: .35rem 0;
}
.aa-document-row {
  padding: .35rem 0 .45rem;
  border-bottom: 1px solid var(--aa-border);
}
.aa-document-row p { margin: 0 !important; line-height: 1.35 !important; }
[data-testid="stChatMessageAvatarUser"],
[data-testid="stChatMessageAvatarAssistant"] {
  background: var(--aa-accent-soft);
  border: 1px solid var(--aa-border);
  color: var(--aa-accent);
}
[data-testid="stExpander"] {
  background: var(--aa-panel);
  border: 1px solid var(--aa-border);
  border-radius: 10px;
  box-shadow: 0 1px 2px rgba(28, 39, 50, .025);
}
[data-testid="stExpander"] details summary {
  background: transparent;
  color: var(--aa-text);
  font-size: .86rem;
  font-weight: 600;
}
[data-testid="stExpander"] details summary:hover { background: var(--aa-accent-soft); }
[data-testid="stExpander"] details summary svg { fill: var(--aa-accent) !important; }
[data-testid="stFileUploader"] { border-radius: 12px; }
.stTextInput input, .stTextArea textarea,
[data-testid="stChatInput"] textarea,
[data-baseweb="select"] > div {
  background: var(--aa-input) !important;
  color: var(--aa-text) !important;
  border-color: var(--aa-border) !important;
}
.stTextInput input::placeholder, .stTextArea textarea::placeholder,
[data-testid="stChatInput"] textarea::placeholder { color: #77776f !important; }
.aa-kicker {
  color: var(--aa-accent);
  font-weight: 700;
  font-size: .68rem;
  letter-spacing: .11em;
  padding: .15rem .25rem .75rem;
}
.aa-muted { color: var(--aa-text-soft); font-size: .82rem; }
.aa-session-meta { color: var(--aa-text-soft); font-size: .72rem; margin-top: -.45rem; }
.right-rail-marker { display: none; }
.aa-rail-header { padding: .2rem .15rem .85rem; }
.aa-rail-title { color: var(--aa-text); font-size: .92rem; font-weight: 650; }
.aa-rail-subtitle { color: var(--aa-text-soft); font-size: .72rem; margin-top: .12rem; }
button[kind="primary"] {
  background: var(--aa-accent) !important;
  border-color: var(--aa-accent) !important;
  color: #ffffff !important;
}
button[kind="secondary"] {
  background: var(--aa-input) !important;
  border-color: var(--aa-border-strong) !important;
  color: var(--aa-text) !important;
}
button[kind="primary"] p, button[kind="primary"] span,
button[kind="secondary"] p, button[kind="secondary"] span {
  color: inherit !important;
}
button[kind="secondary"]:hover {
  background: var(--aa-accent-soft) !important;
  border-color: var(--aa-accent) !important;
}
[data-testid="stSidebar"] button {
  border-radius: 8px;
  min-height: 2.35rem;
  text-align: left;
}
[data-testid="stSidebar"] [data-testid="stPopover"] button {
  text-align: center;
  font-size: 1.05rem;
  font-weight: 700;
}
[data-testid="stSidebar"] [data-testid="stPopoverButton"]
[data-testid="stMarkdownContainer"],
[data-testid="stSidebar"] [data-testid="stPopoverButton"]
> div > div:last-child {
  display: none !important;
}
[data-testid="stSidebarCollapseButton"] button,
[data-testid="stSidebarCollapsedControl"] button,
[data-testid="stExpandSidebarButton"] {
  background: var(--aa-accent) !important;
  color: #ffffff !important;
  border: 1px solid var(--aa-accent) !important;
  border-radius: 8px !important;
  box-shadow: 0 2px 8px var(--aa-shadow);
}
[data-testid="stSidebarCollapseButton"] button svg,
[data-testid="stSidebarCollapsedControl"] button svg,
[data-testid="stExpandSidebarButton"] [data-testid="stIconMaterial"] {
  fill: #ffffff !important;
  color: #ffffff !important;
}
[data-testid="stChatInput"] {
  background: var(--aa-input) !important;
  border: 1px solid var(--aa-border-strong) !important;
  border-radius: 12px !important;
  box-shadow: 0 5px 18px var(--aa-shadow);
}
[data-testid="stChatInput"] > div { background: var(--aa-input) !important; }
[data-testid="stChatInputSubmitButton"] {
  background: var(--aa-accent) !important;
  color: #ffffff !important;
  border-radius: 8px !important;
}
[data-testid="stSidebar"] [data-testid="stTextInput"] label,
div[data-testid="stHorizontalBlock"]:has(.right-rail-marker) label,
div[data-testid="stHorizontalBlock"]:has(.right-rail-marker) p {
  font-size: .8rem;
}
div[data-testid="stHorizontalBlock"]:has(.right-rail-marker)
> div[data-testid="stColumn"]:last-child {
  background: var(--aa-rail);
  border: 1px solid var(--aa-border);
  border-radius: 12px;
  padding: .8rem;
}
button:focus-visible, input:focus-visible, textarea:focus-visible {
  outline: 3px solid rgba(56,95,85,.25) !important;
  outline-offset: 2px;
}
@media (min-width: 1100px) {
  div[data-testid="stHorizontalBlock"]:has(.right-rail-marker)
  > div[data-testid="stColumn"]:last-child {
    position: sticky;
    top: 3.75rem;
    align-self: flex-start;
    max-height: calc(100vh - 4.5rem);
    overflow-y: auto;
    scrollbar-width: thin;
  }
}
@media (max-width: 1099px) {
  .block-container { padding-left: 1rem; padding-right: 1rem; }
  .aa-hero h1 { font-size: 1.7rem; }
  div[data-testid="stHorizontalBlock"]:has(.right-rail-marker) {
    flex-direction: column;
  }
  div[data-testid="stHorizontalBlock"]:has(.right-rail-marker)
  > div[data-testid="stColumn"] { width: 100% !important; }
}
</style>
"""


def _sync_mode_flags() -> None:
    preset = MODE_PRESETS[st.session_state.evaluation_mode]
    st.session_state.enable_memory = preset["enable_memory"]
    st.session_state.enable_verification = preset["enable_verification"]


def _build_orchestrator(runtime: str, top_k: int):
    if runtime == "Real":
        return build_stage1_orchestrator(top_k=top_k)
    return build_mock_orchestrator(top_k=top_k)


def _get_orchestrator(runtime: str, top_k: int):
    key = (runtime, top_k)
    if st.session_state.get("orchestrator_key") != key:
        st.session_state.orchestrator = _build_orchestrator(runtime, top_k)
        st.session_state.orchestrator_key = key
    return st.session_state.orchestrator


def _render_claims(state: AgentState) -> None:
    if not state.claims:
        notice = verification_notice(state)
        if notice:
            getattr(st, notice[0])(notice[1])
        return
    for row in claim_presentations(state):
        source_text = "；".join(
            f"[{source['locator']}]({source['url']})"
            if source["is_web"] and source["url"]
            else source["locator"]
            for source in row["sources"]
        )
        detail = f"{row['score_label']} {row['confidence']:.2f}"
        if source_text:
            detail += f" · 证据：{source_text}"
        if row["note"]:
            detail += f" · {row['note']}"
        title = f"{row['label']} · {row['claim'][:72]}"
        if len(row["claim"]) > 72:
            title += "…"
        with st.expander(title, expanded=False):
            st.markdown('<div class="aa-claim-card">', unsafe_allow_html=True)
            st.markdown(row["claim"])
            st.caption(detail)
            st.markdown("</div>", unsafe_allow_html=True)


def _render_state(
    state: AgentState, *, uploaded_documents: int, indexed_documents: int | None
) -> None:
    st.markdown(state.final_answer)
    citations = used_citations(state)
    summary = answer_panel_summary(state)
    if summary["unsupported"]:
        st.warning(
            f"有 {summary['unsupported']} 条事实性 claim 缺少充分证据；"
            "请展开 Claim Verification 核对。"
        )
    elif summary["unverified"]:
        st.info(
            f"有 {summary['unverified']} 条 claim 暂时无法可靠判断；"
            "可展开校验详情查看原因。"
        )
    if state.errors:
        st.warning(f"本轮有 {len(state.errors)} 条运行异常，详情见“运行信息”。")
    if citations:
        with st.expander(f"来源引用 · {summary['citations']} 条", expanded=False):
            for citation in citations:
                if citation["is_web"] and citation["url"]:
                    st.markdown(
                        f"**[{citation['index']}] "
                        f"[{citation['locator']}]({citation['url']})**"
                    )
                else:
                    st.markdown(
                        f"**[{citation['index']}] {citation['file_name']} "
                        f"第 {citation['page_number']} 页**"
                    )
    st.markdown('<div class="aa-claim-divider"></div>', unsafe_allow_html=True)
    with st.expander(
        f"Claim Verification · {summary['claims']} 条"
        + (f" · {summary['unsupported']} 条需注意" if summary["unsupported"] else ""),
        expanded=False,
    ):
        _render_claims(state)
    with st.expander(
        f"运行信息 · {len(state.evidence)} 条证据"
        + (f" · {summary['errors']} 条异常" if summary["errors"] else ""),
        expanded=False,
    ):
        if state.route:
            st.write("Router:", state.route.route.value, "-", state.route.reason)
        source_level, source_message = source_status(
            state,
            uploaded_documents=uploaded_documents,
            indexed_documents=indexed_documents,
        )[1:]
        getattr(st, source_level)(source_message)
        web_level, web_message = web_search_summary(state)
        getattr(st, web_level)(web_message)
        memory_level, memory_message = memory_write_summary(state)
        getattr(st, memory_level)(memory_message)
        stages = pipeline_stage_presentations(state)
        if stages:
            st.caption("可观测执行阶段（不展示模型内部思考）")
            for stage in stages:
                elapsed = (
                    f" · {stage['latency_ms']:.0f} ms"
                    if stage["latency_ms"]
                    else ""
                )
                detail = f" · {stage['detail']}" if stage["detail"] else ""
                st.write(f"{stage['label']}：{stage['status']}{elapsed}{detail}")
        st.write(
            {
                "tokens": state.usage.total_tokens,
                "llm_calls": state.usage.llm_calls,
                "cost_usd": state.usage.cost_usd,
                "latency_ms": state.usage.latency_ms,
            }
        )
        if state.errors:
            st.warning("；".join(state.errors))


def _save_upload(uploaded: Any) -> Path:
    destination = settings.runtime.data_raw_dir / safe_pdf_name(uploaded.name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(uploaded.getvalue())
    return destination


def _document_statuses() -> dict[str, dict[str, Any]]:
    return st.session_state.setdefault("document_index_status", {})


def _persistent_index_statuses(
    orch: Any, runtime: str
) -> tuple[dict[str, dict[str, Any]], str | None]:
    if runtime != "Real":
        return {}, None
    list_documents = getattr(orch.retriever, "list_indexed_documents", None)
    if not callable(list_documents):
        return {}, "当前 Retriever 不提供持久化索引状态查询。"
    try:
        items = list_documents()
    except Exception as exc:  # noqa: BLE001 - keep the UI available
        return {}, f"{type(exc).__name__}: {exc}"
    return {str(item["file_name"]): dict(item) for item in items}, None


def _document_counts(
    persisted: dict[str, dict[str, Any]], runtime: str
) -> tuple[int, int | None]:
    pdfs = list(settings.runtime.data_raw_dir.glob("*.pdf"))
    if runtime != "Real":
        return len(pdfs), 0
    return len(pdfs), len(persisted)


def _render_upload(orch: Any, runtime: str) -> None:
    uploaded = st.file_uploader(
        "上传 PDF",
        type=["pdf"],
        accept_multiple_files=False,
        key="chat-pdf-upload",
        help="入口位于聊天框附近，长对话滚动到底部即可快速导入。",
    )
    if uploaded is None or not st.button(
        "保存并导入文档", key="chat-pdf-import", use_container_width=True
    ):
        return

    statuses = _document_statuses()
    display_name = safe_pdf_name(uploaded.name)
    path: Path | None = None
    stage = "保存文件"
    with st.status("正在保存 PDF...", expanded=True) as progress:
        try:
            path = _save_upload(uploaded)
            statuses[path.name] = {"status": "saved", "chunks": 0}
            progress.write("✓ 文件已保存，正在检查文字层。")
            stage = "检查 PDF 文字层"
            profile = inspect_pdf_text(path)
            if profile.needs_ocr:
                pages = "、".join(str(page) for page in profile.scanned_pages)
                progress.update(label=f"检测到扫描页 {pages}，正在执行 OCR...")
                progress.write(
                    f"共 {profile.pages} 页；{profile.text_pages} 页有文字层，"
                    f"{len(profile.scanned_pages)} 页需要 OCR。"
                )
            else:
                progress.update(label="PDF 文字提取完成，正在生成 embedding...")
                progress.write(f"✓ {profile.pages} 页均检测到可搜索文字。")

            if runtime == "Real":
                stage = "OCR / embedding / 数据库索引"
                progress.write("正在写入向量索引并刷新 BM25 全量语料。")
                chunks = orch.retriever.index_document(str(path))
                statuses[path.name] = {
                    "status": "indexed",
                    "chunks": chunks,
                    "scanned_pages": list(profile.scanned_pages),
                }
                progress.update(
                    label=f"{path.name} 导入完成：{chunks} 个 chunks",
                    state="complete",
                    expanded=False,
                )
                st.success("已建立索引，可开始提问并核对页码引用。")
            else:
                statuses[path.name] = {"status": "saved", "chunks": 0}
                progress.update(
                    label=f"{path.name} 已保存（Mock 模式不建立索引）",
                    state="complete",
                    expanded=False,
                )
        except Exception as exc:  # noqa: BLE001 - UI must remain usable
            name = path.name if path is not None else display_name
            statuses[name] = {
                "status": "failed",
                "chunks": 0,
                "error": upload_error_message(exc),
                "error_type": type(exc).__name__,
                "stage": stage,
            }
            progress.update(label=f"{name} 导入失败", state="error", expanded=True)
            st.error(f"文档未建立索引：{upload_error_message(exc)}")
            st.caption("文件仍保存在上传目录；修复依赖后可点击同一按钮重试。")


def _render_documents(
    orch: Any,
    runtime: str,
    persisted: dict[str, dict[str, Any]],
    index_error: str | None,
) -> None:
    statuses = _document_statuses()
    paths = {
        path.name: path
        for path in settings.runtime.data_raw_dir.glob("*.pdf")
    }
    names = sorted(set(paths) | set(persisted))
    if index_error:
        st.warning(f"无法读取数据库索引状态：{index_error}")
    if names:
        for name in names:
            path = paths.get(name)
            pages = pdf_page_count(path) if path is not None else None
            row = document_status_presentation(
                pages=pages,
                attempt=statuses.get(name),
                persisted=persisted.get(name),
            )
            icon = "✓" if row["level"] == "success" else "!" if row["level"] == "error" else "•"
            st.markdown(
                f'<div class="aa-document-row"><strong>{icon} {name}</strong><br>'
                f'<span class="aa-muted">{row["label"]}</span></div>',
                unsafe_allow_html=True,
            )
            if runtime == "Real":
                with st.popover(
                    "文档操作",
                    icon=":material/more_horiz:",
                    key=f"document-actions-{name}",
                    use_container_width=True,
                ):
                    st.warning("删除会同时移除数据库索引和上传的 PDF 原文件。")
                    confirmed = st.checkbox(
                        "确认删除", key=f"document-delete-confirm-{name}"
                    )
                    if st.button(
                        "删除文档",
                        key=f"document-delete-{name}",
                        disabled=not confirmed,
                        use_container_width=True,
                    ):
                        try:
                            index_deleted, file_deleted = delete_document_assets(
                                orch.retriever, name, settings.runtime.data_raw_dir
                            )
                            statuses.pop(name, None)
                            if index_deleted or file_deleted:
                                st.success("文档及其可用数据已删除。")
                            else:
                                st.info("未找到可删除的文档数据。")
                            st.rerun()
                        except Exception as exc:  # noqa: BLE001
                            st.error(f"删除失败：{type(exc).__name__}: {exc}")
    else:
        st.caption("尚未上传 PDF。")


def _render_memories(
    orch: Any,
    user_id: str,
    session_id: str,
    enabled: bool,
    latest_state: AgentState | None,
) -> None:
    if not enabled:
        st.info("当前已关闭长期记忆；本轮不会读取或保存。")
        return
    if latest_state is None:
        st.info("本会话尚未运行问题，因此本轮尚未产生记忆保存结果。")
    else:
        level, message = memory_write_summary(latest_state)
        getattr(st, level)(message)
    try:
        memories = orch.memory_store.list_all(user_id=user_id)
    except Exception as exc:  # noqa: BLE001 - database may be offline
        st.error(f"长期记忆读取失败：{type(exc).__name__}: {exc}")
        return
    if not memories:
        st.caption("当前用户暂无已保存的长期记忆。")
        return

    st.success(f"已保存 {len(memories)} 条长期记忆。")

    for memory in memories:
        with st.popover(
            f"{memory.content[:42]}" + ("…" if len(memory.content) > 42 else ""),
            use_container_width=True,
        ):
            st.caption(f"层级：{memory.tier.value}")
            content = st.text_area(
                "内容", memory.content, key=f"memory-content-{memory.memory_id}"
            )
            importance = st.slider(
                "重要度", 0.0, 1.0, float(memory.importance), 0.05,
                key=f"memory-importance-{memory.memory_id}",
            )
            left, right = st.columns(2)
            if left.button("保存修改", key=f"memory-save-{memory.memory_id}"):
                try:
                    updated = orch.memory_store.update(
                        memory.memory_id, content, importance, user_id=user_id
                    )
                    if updated is None:
                        st.warning("该记忆不存在，或不属于当前用户。")
                    else:
                        st.success("记忆已更新。")
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(f"更新失败：{type(exc).__name__}: {exc}")
            if right.button("删除", key=f"memory-delete-{memory.memory_id}"):
                try:
                    deleted = orch.memory_store.delete(memory.memory_id, user_id=user_id)
                    if not deleted:
                        st.warning("该记忆不存在，或不属于当前用户。")
                    else:
                        st.success("记忆已删除。")
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(f"删除失败：{type(exc).__name__}: {exc}")


def _session_store() -> SessionStore:
    if "session_store" not in st.session_state:
        st.session_state.session_store = SessionStore()
    return st.session_state.session_store


def _restore_history(store: SessionStore, user_id: str, session_id: str) -> None:
    states = store.restore(user_id, session_id)
    st.session_state.history = [
        {"question": state.question, "state": state} for state in states
    ]
    st.session_state.loaded_session = (user_id, session_id)


def _new_session_id() -> str:
    return f"chat-{uuid4().hex[:8]}"


def _select_session(session_id: str) -> None:
    st.session_state.pending_session_id = session_id
    st.session_state.loaded_session = None
    st.rerun()


def _session_group(timestamp: str) -> str:
    """Group session timestamps like Codex without changing storage semantics."""
    try:
        day = datetime.strptime(timestamp, "%Y-%m-%d %H:%M").date()
    except (TypeError, ValueError):
        return "更早"
    if day == date.today():
        return "今天"
    if day == date.today() - timedelta(days=1):
        return "昨天"
    return "更早"


def _render_session_actions(
    store: SessionStore,
    *,
    user_id: str,
    item: dict[str, Any],
    session_ids: list[str],
) -> None:
    """Render the stable three-dot alternative to a browser context menu."""
    session_id = item["session_id"]
    with st.popover(
        "更多操作",
        icon=":material/more_horiz:",
        help="重命名、清空或删除会话",
        key=f"session-menu-{user_id}-{session_id}",
        use_container_width=True,
    ):
        st.caption(session_id)
        rename_id = st.text_input(
            "会话名称",
            value=session_id,
            key=f"rename-{user_id}-{session_id}",
        )
        if st.button(
            "重命名",
            key=f"rename-button-{user_id}-{session_id}",
            use_container_width=True,
        ):
            try:
                target = rename_id.strip()
                store.rename(user_id, session_id, target)
                if item["selected"]:
                    _select_session(target)
                st.rerun()
            except (ValueError, OSError) as exc:
                st.error(f"重命名失败：{exc}")
        if st.button(
            "清空对话",
            key=f"clear-button-{user_id}-{session_id}",
            use_container_width=True,
        ):
            store.clear(user_id, session_id)
            store.create(user_id, session_id)
            if item["selected"]:
                st.session_state.history = []
                st.session_state.loaded_session = (user_id, session_id)
            st.rerun()
        confirm_delete = st.checkbox(
            "确认删除", key=f"confirm-delete-{user_id}-{session_id}"
        )
        if st.button(
            "删除会话",
            key=f"delete-button-{user_id}-{session_id}",
            disabled=not confirm_delete,
            use_container_width=True,
        ):
            store.clear(user_id, session_id)
            if item["selected"]:
                remaining = [value for value in session_ids if value != session_id]
                target = remaining[0] if remaining else _new_session_id()
                store.create(user_id, target)
                _select_session(target)
            st.rerun()


def _render_session_sidebar(
    store: SessionStore,
) -> tuple[str, str]:
    """Render a scan-friendly, user-isolated conversation list."""
    with st.sidebar:
        st.markdown('<div class="aa-kicker">ACADEMIC RESEARCH</div>', unsafe_allow_html=True)
        st.subheader("会话")
        user_id = str(st.session_state.user_id).strip()
        try:
            sessions = store.list_sessions(user_id)
        except ValueError as exc:
            st.error(str(exc))
            st.stop()

        current = st.session_state.session_selector
        session_ids = [item["session_id"] for item in sessions]
        if current not in session_ids:
            store.create(user_id, current)
            sessions = store.list_sessions(user_id)
            session_ids = [item["session_id"] for item in sessions]

        if st.button("＋ 新建会话", type="primary", use_container_width=True):
            target = _new_session_id()
            store.create(user_id, target)
            _select_session(target)

        navigation = session_navigation_items(sessions, current)
        grouped = {"今天": [], "昨天": [], "更早": []}
        for item in navigation:
            grouped[_session_group(item["timestamp"])].append(item)
        for group, items in grouped.items():
            if not items:
                continue
            st.caption(group)
            for item in items:
                row, actions = st.columns([4.7, 1.3], gap="small")
                selected = "● " if item["selected"] else ""
                label = f"{selected}{item['summary'][:25]}"
                if row.button(
                    label,
                    key=f"session-open-{user_id}-{item['session_id']}",
                    type="primary" if item["selected"] else "secondary",
                    use_container_width=True,
                    help=(
                        f"{item['session_id']} · {item['turn_count']} 轮"
                        + (f" · {item['timestamp']}" if item["timestamp"] else "")
                    ),
                ) and not item["selected"]:
                    _select_session(item["session_id"])
                with actions:
                    _render_session_actions(
                        store,
                        user_id=user_id,
                        item=item,
                        session_ids=session_ids,
                    )

        session_id = current
    return user_id, session_id


@st.dialog("设置")
def _settings_dialog() -> None:
    st.text_input("用户 ID", key="user_id")
    st.radio("运行链路", ["Mock", "Real"], horizontal=True, key="runtime")
    st.selectbox(
        "评测模式",
        sorted(MODE_PRESETS),
        key="evaluation_mode",
        on_change=_sync_mode_flags,
    )
    st.slider("检索 Top-K", 1, 10, key="top_k")
    st.checkbox("长期记忆", key="enable_memory")
    st.checkbox("Claim Verification", key="enable_verification")
    st.caption("Real 使用 .env 模型；Mock 无需 API 或数据库。")


@st.dialog("长期记忆", width="large")
def _memory_dialog(
    orch: Any,
    user_id: str,
    session_id: str,
    enabled: bool,
    latest_state: AgentState | None,
) -> None:
    _render_memories(orch, user_id, session_id, enabled, latest_state)


def main() -> None:
    st.set_page_config(page_title="Academic Agent", page_icon="A", layout="wide")
    st.markdown(_APP_CSS, unsafe_allow_html=True)
    if "evaluation_mode" not in st.session_state:
        st.session_state.evaluation_mode = "B4"
        _sync_mode_flags()
    st.session_state.setdefault("runtime", "Mock")
    st.session_state.setdefault("top_k", 5)
    st.session_state.setdefault("inspector_open", True)

    store = _session_store()
    st.session_state.setdefault("user_id", settings.runtime.default_user_id)
    st.session_state.setdefault("session_selector", "streamlit")
    pending = st.session_state.pop("pending_session_id", None)
    if pending:
        st.session_state.session_selector = pending
    user_id, session_id = _render_session_sidebar(store)
    runtime = st.session_state.runtime
    mode = st.session_state.evaluation_mode
    top_k = int(st.session_state.top_k)
    enable_memory = bool(st.session_state.enable_memory)
    enable_verification = bool(st.session_state.enable_verification)

    orch = _get_orchestrator(runtime, top_k)
    if st.session_state.get("loaded_session") != (user_id, session_id):
        _restore_history(store, user_id, session_id)
    persisted_indexes, index_status_error = _persistent_index_statuses(
        orch, runtime
    )
    uploaded_documents, indexed_documents = _document_counts(
        persisted_indexes, runtime
    )
    inspector_open = bool(st.session_state.inspector_open)
    chat_col, info_col = st.columns(
        [3.75, 1.15] if inspector_open else [4.7, 0.65], gap="large"
    )

    with chat_col:
        header_space, settings_col, memory_col = st.columns([8, 1, 1.25], gap="small")
        with settings_col:
            if st.button("设置", icon=":material/settings:", use_container_width=True):
                _settings_dialog()
        with memory_col:
            if st.button(
                "长期记忆", icon=":material/memory:", use_container_width=True
            ):
                latest_state = (
                    st.session_state.history[-1]["state"]
                    if st.session_state.history
                    else None
                )
                _memory_dialog(
                    orch, user_id, session_id, enable_memory, latest_state
                )
        st.markdown(
            '<section class="aa-hero">'
            '<div class="aa-hero-kicker">Evidence-grounded research assistant</div>'
            '<h1>Agentic Academic Assistant</h1>'
            '<p>面向课程讲义与研究文献的检索、引用、记忆与 Claim Verification</p>'
            '</section>',
            unsafe_allow_html=True,
        )
        for item in st.session_state.history:
            with st.chat_message("user"):
                st.markdown(item["question"])
            with st.chat_message("assistant"):
                _render_state(
                    item["state"],
                    uploaded_documents=uploaded_documents,
                    indexed_documents=indexed_documents,
                )

        upload_col, input_hint_col = st.columns([1, 5])
        with upload_col:
            with st.popover("＋ 上传 PDF", use_container_width=True):
                _render_upload(orch, runtime)
        with input_hint_col:
            st.caption("上传后可直接提问；失败时旧索引状态会继续保留。")
        question = st.chat_input("请输入文档问题、论文比较或研究任务")
        if question:
            with st.chat_message("user"):
                st.markdown(question)
            flags = dict(MODE_PRESETS[mode])
            flags["enable_memory"] = enable_memory
            flags["enable_verification"] = enable_verification
            try:
                with st.status(
                    "Agent 正在执行路由、检索、生成与校验...", expanded=True
                ) as run_status:
                    st.write("仅展示可核对的执行阶段，不展示模型内部思考。")
                    # Issue #44：把此前轮次作为结构化历史传入，模型才能理解
                    # "它/这个"等指代。只带 question 与最终 answer，不带完整 state。
                    turns = [
                        ConversationTurn(
                            question=item["question"],
                            answer=item["state"].final_answer,
                            turn_index=idx,
                        )
                        for idx, item in enumerate(st.session_state.history)
                    ]
                    state = orch.run(
                        question,
                        session_id=session_id,
                        user_id=user_id,
                        turn_index=len(turns),
                        history=turns,
                        **flags,
                    )
                    run_status.write(
                        f"完成：取得 {len(state.evidence)} 条证据，"
                        f"生成 {len(state.claims)} 条 claim 校验结果。"
                    )
                    run_status.update(
                        label="Agent 处理完成", state="complete", expanded=False
                    )
                st.session_state.history.append({"question": question, "state": state})
                store.append(state, user_id=user_id, session_id=session_id)
                st.rerun()
            except Exception as exc:  # noqa: BLE001 - show recoverable UI error
                st.error(f"运行失败：{type(exc).__name__}: {exc}")

    with info_col:
        st.markdown('<span class="right-rail-marker"></span>', unsafe_allow_html=True)
        if inspector_open:
            rail_title, rail_toggle = st.columns([3.8, 1.5], gap="small")
            with rail_title:
                st.markdown(
                    '<div class="aa-rail-header"><div class="aa-rail-title">文档资料</div>'
                    '<div class="aa-rail-subtitle">固定在右侧，可独立展开</div></div>',
                    unsafe_allow_html=True,
                )
            if rail_toggle.button(
                "×",
                key="inspector-close",
                help="收起文档资料",
                use_container_width=True,
            ):
                st.session_state.inspector_open = False
                st.rerun()
            with st.expander(
                f"文档状态 · {uploaded_documents} 个文件", expanded=True
            ):
                _render_documents(
                    orch, runtime, persisted_indexes, index_status_error
                )
        elif st.button(
            "展开",
            key="inspector-open",
            help="展开文档资料",
            use_container_width=True,
        ):
            st.session_state.inspector_open = True
            st.rerun()


if __name__ == "__main__":
    main()
