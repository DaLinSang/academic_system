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
    memory_write_summary,
    pdf_page_count,
    safe_pdf_name,
    source_status,
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


def _render_state(state: AgentState) -> None:
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
        source_level, source_message = source_status(state)[1:]
        getattr(st, source_level)(source_message)
        web_level, web_message = web_search_summary(state)
        getattr(st, web_level)(web_message)
        memory_level, memory_message = memory_write_summary(state)
        getattr(st, memory_level)(memory_message)
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


def _render_documents(orch: Any, runtime: str) -> None:
    st.subheader("文档")
    uploaded = st.file_uploader("上传 PDF", type=["pdf"], accept_multiple_files=False)
    if uploaded is not None and st.button("保存并导入文档", use_container_width=True):
        try:
            path = _save_upload(uploaded)
            if runtime == "Real":
                with st.spinner("正在解析并建立索引..."):
                    chunks = orch.retriever.index_document(str(path))
                st.success(f"{path.name} 已导入，共 {chunks} 个 chunks。")
            else:
                st.success(f"{path.name} 已保存。Mock 模式不会建立真实索引。")
        except Exception as exc:  # noqa: BLE001 - UI must remain usable
            st.error(f"文档导入失败：{type(exc).__name__}: {exc}")

    pdfs = sorted(settings.runtime.data_raw_dir.glob("*.pdf"))
    if pdfs:
        for path in pdfs:
            pages = pdf_page_count(path)
            page_text = f" · {pages} 页" if pages is not None else " · 页数不可读"
            st.caption(f"• {path.name}{page_text}")
    else:
        st.caption("尚未上传 PDF。")


def _render_memories(orch: Any, user_id: str, enabled: bool) -> None:
    st.subheader("长期记忆")
    if not enabled:
        st.caption("当前已关闭长期记忆。")
        return
    try:
        memories = orch.memory_store.list_all(user_id=user_id)
    except Exception as exc:  # noqa: BLE001 - database may be offline
        st.warning(f"暂时无法读取记忆：{type(exc).__name__}: {exc}")
        return
    if not memories:
        st.caption("当前没有保存的记忆。")
        return

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
                    orch.memory_store.update(memory.memory_id, content, importance)
                    st.success("记忆已更新。")
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(f"更新失败：{type(exc).__name__}: {exc}")
            if right.button("删除", key=f"memory-delete-{memory.memory_id}"):
                try:
                    orch.memory_store.delete(memory.memory_id)
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

    chat_col, info_col = st.columns([2, 1])
    with chat_col:
        st.subheader("对话")
        for item in st.session_state.history:
            with st.chat_message("user"):
                st.markdown(item["question"])
            with st.chat_message("assistant"):
                _render_state(item["state"])

        question = st.chat_input("请输入文档问题、论文比较或研究任务")
        if question:
            flags = dict(MODE_PRESETS[mode])
            flags["enable_memory"] = enable_memory
            flags["enable_verification"] = enable_verification
            try:
                with st.spinner("Agent 正在处理..."):
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
                st.session_state.history.append({"question": question, "state": state})
                store.append(state, user_id=user_id, session_id=session_id)
                st.rerun()
            except Exception as exc:  # noqa: BLE001 - show recoverable UI error
                st.error(f"运行失败：{type(exc).__name__}: {exc}")

    with info_col:
        _render_documents(orch, runtime)
        _render_memories(orch, user_id, enable_memory)


if __name__ == "__main__":
    main()
