"""契约扩展的回归测试（Issue #39）。

向后兼容是硬要求：不传新字段时，所有既有行为必须完全不变。
"""

from __future__ import annotations

import json

import pytest

from app.core.types import (
    AgentState,
    ConversationTurn,
    Evidence,
    RetrievalMethod,
)


def doc_evidence() -> Evidence:
    return Evidence(
        chunk_id="c1",
        document_id="d1",
        file_name="Lec04.pdf",
        page_number=9,
        text="RAG 段落",
        score=0.5,
        retrieval_method=RetrievalMethod.RRF,
    )


def web_evidence() -> Evidence:
    return Evidence(
        chunk_id="w1",
        document_id="https://example.com/rag",
        file_name="example.com",
        page_number=0,
        text="网页正文",
        score=0.7,
        retrieval_method=RetrievalMethod.WEB,
        url="https://example.com/rag",
        title="What is RAG",
    )


# --------------------------------------------------------------------------- #
# 文档证据：行为不变
# --------------------------------------------------------------------------- #
def test_document_locator_unchanged():
    assert doc_evidence().locator == "Lec04.pdf p.9"


def test_document_evidence_has_no_url_by_default():
    assert doc_evidence().url is None
    assert doc_evidence().is_web is False


def test_page_number_still_accepts_positive_values():
    assert doc_evidence().page_number == 9


# --------------------------------------------------------------------------- #
# 网页证据：新通道
# --------------------------------------------------------------------------- #
def test_web_method_exists():
    assert RetrievalMethod.WEB.value == "web"


def test_web_locator_uses_title_not_page():
    assert web_evidence().locator == "What is RAG · 网页"


def test_web_locator_falls_back_to_file_name():
    item = web_evidence()
    item.title = None
    assert item.locator == "example.com · 网页"


def test_web_evidence_allows_zero_page():
    item = web_evidence()
    assert item.page_number == 0


def test_web_evidence_is_flagged():
    assert web_evidence().is_web is True


def test_web_evidence_serializes_url():
    data = json.loads(web_evidence().model_dump_json())
    assert data["url"] == "https://example.com/rag"
    assert data["retrieval_method"] == "web"


# --------------------------------------------------------------------------- #
# 多轮：新字段默认不影响单轮
# --------------------------------------------------------------------------- #
def test_agent_state_history_defaults_empty():
    assert AgentState(question="hi").history == []


def test_conversation_turn_round_trip():
    turn = ConversationTurn(question="我在做 RAG", answer="好的", turn_index=0)
    state = AgentState(question="它解决什么？", history=[turn])

    data = json.loads(state.model_dump_json())
    assert data["history"][0]["question"] == "我在做 RAG"
    assert data["history"][0]["turn_index"] == 0


def test_history_does_not_leak_into_route_or_evidence():
    turn = ConversationTurn(question="上一问", answer="上一答", turn_index=0)
    state = AgentState(question="本轮", history=[turn])

    assert state.route is None
    assert state.evidence == []
    assert state.final_answer == ""


def test_jsonl_round_trip_with_new_fields():
    state = AgentState(question="q", history=[ConversationTurn(question="p", answer="a", turn_index=0)])
    state.evidence = [web_evidence()]
    line = state.model_dump_json()

    restored = AgentState.model_validate_json(line)
    assert restored.history[0].question == "p"
    assert restored.evidence[0].locator == "What is RAG · 网页"


# --------------------------------------------------------------------------- #
# C review #54：Citation 与来源不变量
# --------------------------------------------------------------------------- #
def test_web_evidence_builds_citation_without_crash():
    """端到端：Web Evidence -> build_citations() -> 可点击网页来源。"""
    state = AgentState(question="q")
    state.evidence = [web_evidence()]

    citations = state.build_citations()

    assert len(citations) == 1
    assert citations[0].is_web is True
    assert citations[0].url == "https://example.com/rag"
    assert citations[0].locator == "What is RAG · 网页"
    assert citations[0].page_number == 0, "不能伪造 p.1"


def test_document_citation_still_has_page():
    state = AgentState(question="q")
    state.evidence = [doc_evidence()]
    citation = state.build_citations()[0]

    assert citation.is_web is False
    assert citation.page_number == 9
    assert citation.locator == "Lec04.pdf p.9"


def test_web_evidence_requires_url():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="url"):
        Evidence(
            chunk_id="w", document_id="d", file_name="site.test", page_number=0,
            text="t", score=0.5, retrieval_method=RetrievalMethod.WEB,
        )


def test_document_evidence_rejects_zero_page():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="page_number"):
        Evidence(
            chunk_id="c", document_id="d", file_name="f.pdf", page_number=0,
            text="t", score=0.5, retrieval_method=RetrievalMethod.RRF,
        )


def test_citation_rejects_web_with_fake_page():
    from pydantic import ValidationError

    from app.core.types import Citation

    with pytest.raises(ValidationError, match="page_number"):
        Citation(index=1, chunk_id="w", document_id="d", file_name="site.test",
                 page_number=1, url="https://example.com")


def test_citation_rejects_document_without_page():
    from pydantic import ValidationError

    from app.core.types import Citation

    with pytest.raises(ValidationError, match="page_number"):
        Citation(index=1, chunk_id="c", document_id="d", file_name="f.pdf",
                 page_number=0)
