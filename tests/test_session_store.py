"""SessionStore 的测试（Issue #45）。

重点守三条：
1. 用户/会话隔离——不能读到别人的聊天；
2. 路径穿越与敏感信息——聊天记录不能成为新的泄露面；
3. 与 RunLogger 的边界——两个目录、两种用途，不混。
"""

from __future__ import annotations

import json

import pytest

from app.agent.session_store import SessionStore
from app.core.types import AgentState, RouteDecision, RouteType, Usage


def make_state(question: str, turn: int = 0, *, answer: str = "回答") -> AgentState:
    state = AgentState(
        question=question,
        user_id="u1",
        session_id="s1",
        turn_index=turn,
    )
    state.route = RouteDecision(route=RouteType.DIRECT)
    state.final_answer = answer
    state.usage = Usage(total_tokens=42, llm_calls=1)
    return state


# --------------------------------------------------------------------------- #
# 基本的写与读
# --------------------------------------------------------------------------- #
def test_append_creates_user_scoped_file(tmp_path):
    store = SessionStore(tmp_path)
    path = store.append(make_state("什么是 RAG？"))

    assert path == tmp_path / "u1" / "s1.jsonl"
    assert path.is_file()


def test_load_returns_rounds_in_order(tmp_path):
    store = SessionStore(tmp_path)
    store.append(make_state("第一问", 0))
    store.append(make_state("第二问", 1))

    rows = store.load("u1", "s1")
    assert [r["question"] for r in rows] == ["第一问", "第二问"]
    assert [r["turn_index"] for r in rows] == [0, 1]


def test_load_missing_session_returns_empty(tmp_path):
    assert SessionStore(tmp_path).load("u1", "not-exist") == []


def test_create_keeps_empty_session_visible(tmp_path):
    store = SessionStore(tmp_path)
    path = store.create("u1", "new-chat")

    assert path.is_file()
    assert store.list_sessions("u1")[0]["session_id"] == "new-chat"
    assert store.list_sessions("u1")[0]["turn_count"] == 0


def test_record_contains_report_fields(tmp_path):
    store = SessionStore(tmp_path)
    store.append(make_state("什么是 RAG？", answer="RAG 是检索增强生成 [1]。"))

    row = store.load("u1", "s1")[0]
    for key in ("question", "final_answer", "route", "usage", "trace", "errors",
                "started_at", "saved_at", "evidence_count", "citation_count"):
        assert key in row, f"缺少字段 {key}"
    assert row["route"]["route"] == "direct"
    assert row["usage"]["total_tokens"] == 42


# --------------------------------------------------------------------------- #
# 用户 / 会话隔离
# --------------------------------------------------------------------------- #
def test_sessions_are_isolated_by_user(tmp_path):
    store = SessionStore(tmp_path)
    store.append(make_state("我的问题"))
    state = make_state("别人的问题")
    state.user_id = "u2"
    store.append(state)

    assert len(store.load("u1", "s1")) == 1
    assert store.load("u2", "s1")[0]["question"] == "别人的问题"


def test_sessions_are_isolated_by_session_id(tmp_path):
    store = SessionStore(tmp_path)
    store.append(make_state("会话一"))
    store.append(make_state("会话二"), session_id="s2")

    assert store.load("u1", "s1")[0]["question"] == "会话一"
    assert store.load("u1", "s2")[0]["question"] == "会话二"


def test_list_sessions_shows_counts_and_orders_by_time(tmp_path):
    store = SessionStore(tmp_path)
    store.append(make_state("一"))
    store.append(make_state("二"))
    store.append(make_state("三"), session_id="s2")

    sessions = store.list_sessions("u1")
    assert [s["session_id"] for s in sessions] == ["s2", "s1"]
    assert sessions[1]["turn_count"] == 2
    assert sessions[0]["last_question"] == "三"


def test_clear_only_removes_one_session(tmp_path):
    store = SessionStore(tmp_path)
    store.append(make_state("一"))
    store.append(make_state("二"), session_id="s2")

    assert store.clear("u1", "s1") is True
    assert store.load("u1", "s1") == []
    assert len(store.load("u1", "s2")) == 1
    assert store.clear("u1", "s1") is False, "重复删除返回 False"


def test_rename_preserves_turns_and_rejects_collision(tmp_path):
    store = SessionStore(tmp_path)
    store.append(make_state("保留的问题"))
    store.create("u1", "occupied")

    destination = store.rename("u1", "s1", "renamed")
    assert destination.name == "renamed.jsonl"
    assert store.load("u1", "s1") == []
    assert store.load("u1", "renamed")[0]["question"] == "保留的问题"
    with pytest.raises(FileExistsError):
        store.rename("u1", "renamed", "occupied")


# --------------------------------------------------------------------------- #
# 安全
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad", ["../etc/passwd", "a/b", "", "具有空格的 id", "x" * 65])
def test_rejects_unsafe_ids(tmp_path, bad):
    store = SessionStore(tmp_path)
    with pytest.raises(ValueError):
        store._path(bad, "s1")
    with pytest.raises(ValueError):
        store._path("u1", bad)


def test_sensitive_keys_are_scrubbed(tmp_path):
    store = SessionStore(tmp_path)
    state = make_state("请记住 api_key=sk-secret123")
    state.errors = ["Authorization: Bearer abc123"]

    store.append(state)
    raw = (tmp_path / "u1" / "s1.jsonl").read_text(encoding="utf-8")

    assert "sk-secret123" not in raw
    assert "Bearer abc123" not in raw
    assert "[已脱敏]" in raw, "脱敏后要留下痕迹，便于知道这里有敏感内容"


def test_trace_secrets_are_scrubbed(tmp_path):
    store = SessionStore(tmp_path)
    state = make_state("问题")
    state.log("router", "decision", api_key="sk-should-not-appear", route="direct")

    store.append(state)
    raw = (tmp_path / "u1" / "s1.jsonl").read_text(encoding="utf-8")
    assert "sk-should-not-appear" not in raw


def test_records_are_valid_jsonl(tmp_path):
    store = SessionStore(tmp_path)
    store.append(make_state("一"))
    store.append(make_state("二"))

    lines = (tmp_path / "u1" / "s1.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert all(json.loads(line) for line in lines)


# --------------------------------------------------------------------------- #
# 与 RunLogger 的边界
# --------------------------------------------------------------------------- #
def test_sessions_live_under_own_directory_not_run_id(tmp_path):
    store = SessionStore(tmp_path)
    path = store.append(make_state("问题"))

    # 形状是 runs/sessions/<user>/<session>.jsonl，不是 runs/<run_id>/samples.jsonl
    assert path.parent.parent == tmp_path
    assert path.name == "s1.jsonl"
    assert not (tmp_path / "samples.jsonl").exists()


# --------------------------------------------------------------------------- #
# C review #51：重启后仍能呈现引用与 claim 证据定位
# --------------------------------------------------------------------------- #
def test_restore_keeps_citations_and_claim_sources(tmp_path):
    from app.core.types import (
        Citation,
        ClaimCheck,
        ClaimStatus,
        Evidence,
        RetrievalMethod,
    )

    store = SessionStore(tmp_path)
    state = make_state("讲义里 RAG 是什么？")
    state.evidence = [
        Evidence(
            chunk_id="c42", document_id="d1", file_name="Lec04.pdf",
            page_number=42, text="RAG 的原文", score=0.9,
            retrieval_method=RetrievalMethod.RRF,
        )
    ]
    state.citations = [Citation.from_evidence(1, state.evidence[0], quote="RAG 的原文")]
    state.claims = [
        ClaimCheck(
            claim="RAG 结合检索与生成",
            status=ClaimStatus.SUPPORTED,
            supporting_evidence=["c42"],
        )
    ]
    store.append(state)

    # 模拟服务重启：新建实例重新读盘
    reopened = SessionStore(tmp_path)
    restored = reopened.restore("u1", "s1")

    assert len(restored) == 1
    row = restored[0]
    assert row.evidence[0].chunk_id == "c42"
    assert row.evidence[0].page_number == 42
    assert row.citations[0].file_name == "Lec04.pdf"
    assert row.citations[0].page_number == 42
    assert row.claims[0].supporting_evidence == ["c42"]
    assert row.final_answer == "回答"
    assert row.enable_memory is True
    assert row.enable_verification is True


def test_restore_preserves_mode_flags_and_trace(tmp_path):
    store = SessionStore(tmp_path)
    state = make_state("问题")
    state.enable_memory = False
    state.enable_verification = False
    state.log("router", "decision", route="direct")
    store.append(state)

    restored = SessionStore(tmp_path).restore("u1", "s1")[0]
    assert restored.enable_memory is False
    assert restored.enable_verification is False
    assert any(e.stage == "router" for e in restored.trace)


def test_load_exposes_full_payload_not_just_counts(tmp_path):
    from app.core.types import Citation, Evidence, RetrievalMethod

    store = SessionStore(tmp_path)
    state = make_state("q")
    state.evidence = [
        Evidence(chunk_id="c1", document_id="d", file_name="f.pdf",
                 page_number=1, text="t", score=0.5,
                 retrieval_method=RetrievalMethod.VECTOR)
    ]
    state.citations = [Citation.from_evidence(1, state.evidence[0])]
    store.append(state)

    raw = store.load("u1", "s1")[0]
    assert "evidence" in raw and "citations" in raw
    assert raw["evidence"][0]["file_name"] == "f.pdf"
    assert raw["citations"][0]["page_number"] == 1
    assert "draft_answer" in raw
    assert "enable_memory" in raw


# --------------------------------------------------------------------------- #
