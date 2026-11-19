"""阶段 0 的契约测试。

这些测试的作用不是"测 Mock 对不对"，而是**锁住公共接口**：
只要有人改了数据结构或接口签名而没同步，这里立刻会红。

运行：
    uv run pytest -v
"""

from __future__ import annotations

import json

import pytest

from app.agent.factory import build_mock_orchestrator
from app.agent.orchestrator import MODE_PRESETS
from app.core.interfaces import (
    LLM,
    AnswerGenerator,
    MemoryManager,
    MemoryStore,
    Planner,
    Retriever,
    Router,
    Verifier,
)
from app.core.mocks import (
    MockAnswerGenerator,
    MockLLM,
    MockMemoryManager,
    MockMemoryStore,
    MockPlanner,
    MockRetriever,
    MockRouter,
    MockVerifier,
)
from app.core.types import (
    AgentState,
    ClaimStatus,
    DocumentChunk,
    Evidence,
    MemoryCandidate,
    MemoryTier,
    RetrievalMethod,
)


# --------------------------------------------------------------------------- #
# 接口一致性：Mock 必须满足 Protocol，否则 B/C 无法替换
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("impl", "proto"),
    [
        (MockRetriever(), Retriever),
        (MockMemoryStore(), MemoryStore),
        (MockMemoryManager(), MemoryManager),
        (MockRouter(), Router),
        (MockPlanner(), Planner),
        (MockVerifier(), Verifier),
        (MockLLM(), LLM),
        (MockAnswerGenerator(), AnswerGenerator),
    ],
)
def test_mock_satisfies_protocol(impl, proto):
    assert isinstance(impl, proto), f"{type(impl).__name__} 不再满足 {proto.__name__} 接口"


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #
def test_document_chunk_requires_page_and_text():
    chunk = DocumentChunk(
        chunk_id="d1:p1:c0",
        document_id="d1",
        file_name="a.pdf",
        page_number=1,
        text="hello",
    )
    assert chunk.page_number == 1

    with pytest.raises(ValueError):
        DocumentChunk(
            chunk_id="d1:p0:c0",
            document_id="d1",
            file_name="a.pdf",
            page_number=0,  # 页码必须 1-based
            text="hello",
        )

    with pytest.raises(ValueError):
        DocumentChunk(
            chunk_id="d1:p1:c0",
            document_id="d1",
            file_name="a.pdf",
            page_number=1,
            text="   ",  # 空文本不允许
        )


def test_evidence_from_chunk_keeps_locator():
    chunk = DocumentChunk(
        chunk_id="d1:p12:c3",
        document_id="d1",
        file_name="Lec04.pdf",
        page_number=12,
        text="RAG combines parametric and non-parametric memory.",
    )
    ev = Evidence.from_chunk(chunk, score=0.87, method=RetrievalMethod.VECTOR)
    assert ev.locator == "Lec04.pdf p.12"
    assert ev.page_number == 12


def test_agent_state_json_roundtrip():
    state = AgentState(question="q")
    payload = json.loads(state.model_dump_json())
    assert payload["question"] == "q"
    restored = AgentState.model_validate(payload)
    assert restored == state


def test_agent_state_add_evidence_dedupes():
    state = AgentState(question="q")
    ev = Evidence(
        chunk_id="c1",
        document_id="d1",
        file_name="a.pdf",
        page_number=3,
        text="t",
        score=1.0,
    )
    state.add_evidence([ev, ev])
    assert len(state.evidence) == 1


def test_citations_numbered_from_one():
    state = AgentState(question="q")
    state.add_evidence(
        [
            Evidence(chunk_id=f"c{i}", document_id="d", file_name="a.pdf",
                     page_number=i, text="t", score=1.0)
            for i in range(1, 4)
        ]
    )
    cites = state.build_citations()
    assert [c.index for c in cites] == [1, 2, 3]
    assert cites[2].page_number == 3


# --------------------------------------------------------------------------- #
# 端到端：四种模式必须真的不同
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("mode", sorted(MODE_PRESETS))
def test_pipeline_runs_in_every_mode(mode):
    orch = build_mock_orchestrator()
    state = orch.run("讲义里 RAG 是什么？", **MODE_PRESETS[mode])
    assert state.final_answer.strip()
    assert state.errors == []
    assert state.route is not None


def test_b1_does_not_retrieve_but_b2_does():
    orch = build_mock_orchestrator()
    q = "比较 RAG 和 ReAct 的方法差异"

    b1 = orch.run(q, **MODE_PRESETS["B1"])
    b2 = orch.run(q, **MODE_PRESETS["B2"])

    assert b1.evidence == [], "B1 不应该触发检索"
    assert b1.citations == []
    assert b2.evidence, "B2 必须触发检索"
    # B1/B2 都不开校验
    assert b1.claims == [] and b2.claims == []


def test_b4_produces_claims_and_flags_unsupported():
    orch = build_mock_orchestrator()
    state = orch.run("讲义里 Self-RAG 是怎么决定要不要检索的？", **MODE_PRESETS["B4"])
    assert state.claims, "B4 必须产出 claim"
    # MockLLM 的固定文本里有一句"该方法在三个数据集上取得了最优结果"，应当被抓出来
    statuses = {c.status for c in state.claims}
    assert ClaimStatus.SUPPORTED in statuses


def test_verifier_without_evidence_marks_all_unsupported():
    verifier = MockVerifier()
    claims = verifier.verify("这是一个没有任何证据支持的陈述。", evidence=[])
    assert claims
    assert all(c.status == ClaimStatus.UNSUPPORTED for c in claims)


def test_verifier_skips_meta_sentences():
    """引导语、结语、纯引用标记不能被当成 claim。"""
    verifier = MockVerifier()
    evidence = [
        Evidence(chunk_id="c1", document_id="d", file_name="a.pdf",
                 page_number=1, text="RAG 使用外部文档索引", score=1.0)
    ]
    answer = "根据检索到的文档：\n- RAG 使用外部文档索引 [1]（a.pdf 第 1 页）\n以上结论均可核对。"
    claims = verifier.verify(answer, evidence)
    texts = [c.claim for c in claims]
    assert all("以上结论" not in t for t in texts)
    assert all("根据检索到的文档" not in t for t in texts)


# --------------------------------------------------------------------------- #
# 记忆
# --------------------------------------------------------------------------- #
def test_memory_crud():
    store = MockMemoryStore()
    before = len(store.list_all())

    item = store.add(
        MemoryCandidate(tier=MemoryTier.PROFILE, content="用户在读 COMP7608B", importance=0.9)
    )
    assert len(store.list_all()) == before + 1

    updated = store.update(item.memory_id, content="用户在读 COMP7608B 且方向是 Agent")
    assert updated is not None and "Agent" in updated.content

    assert store.delete(item.memory_id) is True
    assert store.delete(item.memory_id) is False
    assert len(store.list_all()) == before


def test_mock_memory_update_and_delete_are_user_scoped():
    store = MockMemoryStore()
    item = store.add(
        MemoryCandidate(tier=MemoryTier.PREFERENCE, content="偏好中文回答"),
        user_id="alice",
    )

    assert store.update(item.memory_id, content="被越权修改", user_id="bob") is None
    assert store.delete(item.memory_id, user_id="bob") is False
    assert store.list_all("alice")[0].content == "偏好中文回答"

    updated = store.update(item.memory_id, content="偏好简短中文回答", user_id="alice")
    assert updated is not None and updated.content == "偏好简短中文回答"
    assert store.delete(item.memory_id, user_id="alice") is True


def test_memory_manager_only_stores_stable_statements():
    mgr = MockMemoryManager()
    assert mgr.extract_candidates("今天天气不错", "好的") == []
    got = mgr.extract_candidates("我偏好中文回答", "好的")
    assert len(got) == 1
    assert got[0].tier == MemoryTier.PREFERENCE


# --------------------------------------------------------------------------- #
# 错误恢复：工具挂掉时链路不能崩
# --------------------------------------------------------------------------- #
class _BrokenRetriever:
    def retrieve(self, query: str, top_k: int = 5):
        raise RuntimeError("db down")

    def index_document(self, pdf_path: str, document_id: str | None = None) -> int:
        raise RuntimeError("db down")


def test_retrieval_failure_degrades_gracefully():
    orch = build_mock_orchestrator()
    orch.retriever = _BrokenRetriever()

    state = orch.run("讲义里 RAG 是什么？", **MODE_PRESETS["B4"])

    assert state.final_answer.strip(), "检索失败也必须给出可读的回答"
    assert any("retrieval" in e for e in state.errors)
