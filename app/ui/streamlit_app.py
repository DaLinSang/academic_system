"""Member C's Streamlit UI for the academic assistant."""

from __future__ import annotations

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
    claim_presentations,
    document_status_presentation,
    inspect_pdf_text,
    memory_write_summary,
    pdf_page_count,
    pipeline_stage_presentations,
    safe_pdf_name,
    source_status,
    upload_error_message,
    used_citations,
    verification_notice,
    web_search_summary,
)


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
        message = f"**{row['label']}** · {row['claim']}\n\n{detail}"
        getattr(st, row["level"])(message)


def _render_state(
    state: AgentState, *, uploaded_documents: int, indexed_documents: int | None
) -> None:
    st.markdown(state.final_answer)
    citations = used_citations(state)
    if citations:
        with st.expander("来源引用", expanded=True):
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
                if citation["quote"]:
                    st.caption(citation["quote"])
    with st.expander("Claim Verification", expanded=True):
        _render_claims(state)
    with st.expander("运行信息"):
        if state.route:
            st.write("Router:", state.route.route.value, "-", state.route.reason)
        st.caption(f"用户：{state.user_id} · 会话：{state.session_id} · 轮次：{state.turn_index}")
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
    persisted: dict[str, dict[str, Any]], index_error: str | None
) -> None:
    st.subheader("文档状态")
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
            st.caption(f"• {name} · {row['label']}")
            if row["detail"]:
                st.caption(f"  {row['detail']}")
    else:
        st.caption("尚未上传 PDF。")


def _render_memories(
    orch: Any,
    user_id: str,
    session_id: str,
    enabled: bool,
    latest_state: AgentState | None,
) -> None:
    st.subheader("长期记忆")
    st.caption(f"当前用户：{user_id} · 当前会话：{session_id}")
    st.caption("用户 ID 仅用于数据隔离，不是登录或身份认证。")
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

    st.success(f"当前用户已保存 {len(memories)} 条长期记忆。")

    for memory in memories:
        with st.expander(f"[{memory.tier.value}] {memory.content[:36]}"):
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


def main() -> None:
    st.set_page_config(page_title="Academic Agent", page_icon="A", layout="wide")
    st.title("Agentic Academic Assistant")
    st.caption("文档问答 · 页码引用 · 长期记忆 · Claim Verification")
    if "evaluation_mode" not in st.session_state:
        st.session_state.evaluation_mode = "B4"
        _sync_mode_flags()

    store = _session_store()
    st.session_state.setdefault("user_id", settings.runtime.default_user_id)
    st.session_state.setdefault("session_selector", "streamlit")
    pending = st.session_state.pop("pending_session_id", None)
    if pending:
        st.session_state.session_selector = pending

    with st.sidebar:
        st.header("身份与会话")
        user_id = st.text_input("用户 ID", key="user_id").strip()
        try:
            sessions = store.list_sessions(user_id)
        except ValueError as exc:
            st.error(str(exc))
            st.stop()
        session_ids = [item["session_id"] for item in sessions]
        current = st.session_state.session_selector
        if current not in session_ids:
            store.create(user_id, current)
            session_ids.insert(0, current)
        session_id = st.selectbox("当前会话", session_ids, key="session_selector")

        new_id = st.text_input("新会话 ID", placeholder="留空则自动生成")
        if st.button("创建会话", use_container_width=True):
            target = new_id.strip() or _new_session_id()
            try:
                store.create(user_id, target)
                st.session_state.pending_session_id = target
                st.session_state.loaded_session = None
                st.rerun()
            except (ValueError, OSError) as exc:
                st.error(f"创建失败：{exc}")

        rename_id = st.text_input("重命名当前会话", value=session_id)
        rename_col, delete_col = st.columns(2)
        if rename_col.button("重命名", use_container_width=True):
            try:
                store.rename(user_id, session_id, rename_id.strip())
                st.session_state.pending_session_id = rename_id.strip()
                st.session_state.loaded_session = None
                st.rerun()
            except (ValueError, OSError) as exc:
                st.error(f"重命名失败：{exc}")
        if delete_col.button("删除", use_container_width=True):
            store.clear(user_id, session_id)
            remaining = [item for item in session_ids if item != session_id]
            target = remaining[0] if remaining else _new_session_id()
            store.create(user_id, target)
            st.session_state.pending_session_id = target
            st.session_state.loaded_session = None
            st.rerun()

        st.caption(f"工作区：共享文档库 · 会话：{session_id}")
        st.divider()
        st.header("运行设置")
        runtime = st.radio("运行链路", ["Mock", "Real"], horizontal=True)
        mode = st.selectbox(
            "评测模式",
            sorted(MODE_PRESETS),
            key="evaluation_mode",
            on_change=_sync_mode_flags,
        )
        top_k = st.slider("检索 Top-K", 1, 10, 3)
        enable_memory = st.checkbox("启用长期记忆", key="enable_memory")
        enable_verification = st.checkbox(
            "启用 Claim Verification",
            key="enable_verification",
        )
        st.caption("Real 模式使用 .env 中配置的模型；Mock 模式无需 API 或数据库。")
        if st.button("清空当前会话", use_container_width=True):
            store.clear(user_id, session_id)
            store.create(user_id, session_id)
            st.session_state.history = []
            st.session_state.loaded_session = (user_id, session_id)
            st.rerun()

    orch = _get_orchestrator(runtime, top_k)
    if st.session_state.get("loaded_session") != (user_id, session_id):
        _restore_history(store, user_id, session_id)
    persisted_indexes, index_status_error = _persistent_index_statuses(
        orch, runtime
    )
    uploaded_documents, indexed_documents = _document_counts(
        persisted_indexes, runtime
    )

    chat_col, info_col = st.columns([2, 1])
    with chat_col:
        st.subheader("对话")
        for item in st.session_state.history:
            with st.chat_message("user"):
                st.markdown(item["question"])
            with st.chat_message("assistant"):
                _render_state(
                    item["state"],
                    uploaded_documents=uploaded_documents,
                    indexed_documents=indexed_documents,
                )

        with st.expander("上传或重新索引 PDF", expanded=False):
            _render_upload(orch, runtime)
        question = st.chat_input("请输入文档问题、论文比较或研究任务")
        if question:
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
        _render_documents(persisted_indexes, index_status_error)
        latest_state = (
            st.session_state.history[-1]["state"]
            if st.session_state.history
            else None
        )
        _render_memories(orch, user_id, session_id, enable_memory, latest_state)


if __name__ == "__main__":
    main()
