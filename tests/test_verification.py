from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agent.factory import build_mock_orchestrator
from app.agent.orchestrator import MODE_PRESETS
from app.core.interfaces import Verifier
from app.core.types import (
    AgentState,
    ClaimStatus,
    ConversationTurn,
    Evidence,
    LLMResponse,
    RetrievalMethod,
    RouteDecision,
    RouteType,
    Usage,
)
from app.verification import (
    LexicalVerifier,
    SemanticVerifier,
    VerificationSourceType,
    build_verification_context,
    is_insufficient_answer,
    is_nonfactual_claim,
    split_claim_spans,
    split_claims,
)

_CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "claim_verification_cases.json").read_text(
        encoding="utf-8"
    )
)


def _evidence(text: str, chunk_id: str = "doc:p2:c0", page: int = 2) -> Evidence:
    return Evidence(
        chunk_id=chunk_id,
        document_id="doc",
        file_name="mock.pdf",
        page_number=page,
        text=text,
        score=1.0,
        retrieval_method=RetrievalMethod.MOCK,
    )


class _JSONLLM:
    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.messages = []

    def generate(self, messages, temperature=0.0, max_tokens=None, json_mode=False):
        self.messages = messages
        assert json_mode is True
        return LLMResponse(
            text=self.payload,
            model="semantic-test",
            usage=Usage(total_tokens=42, llm_calls=1, latency_ms=5),
        )


def _semantic_result(
    status: str,
    *,
    confidence: float = 0.9,
    evidence_indices: list[int] | None = None,
    can_determine: bool = True,
    reason: str = "checked semantically",
) -> str:
    return json.dumps(
        {
            "results": [
                {
                    "claim_index": 0,
                    "status": status,
                    "confidence": confidence,
                    "evidence_indices": evidence_indices or [],
                    "can_determine": can_determine,
                    "reason": reason,
                }
            ]
        }
    )


def test_verifier_satisfies_protocol() -> None:
    assert isinstance(LexicalVerifier(), Verifier)


def test_split_claims_filters_meta_and_pure_citations() -> None:
    answer = (
        "根据检索到的文档：\n"
        "- RAG 使用外部文档索引 [1]（mock.pdf 第 2 页）。\n"
        "[1]（mock.pdf 第 2 页）。\n"
        "以上结论均可核对。"
    )
    assert split_claims(answer) == ["RAG 使用外部文档索引 [1]（mock.pdf 第 2 页）"]


def test_split_claims_preserves_semicolon_context_for_t06() -> None:
    answer = (
        "RAG 在事实性方面优于 BART；在不同任务上的比较分别为 "
        "42.7%、7.1% 和 17%。[1]"
    )

    assert split_claims(answer) == [
        "RAG 在事实性方面优于 BART；在不同任务上的比较分别为 "
        "42.7%、7.1% 和 17% [1]"
    ]


def test_split_claims_excludes_insufficient_evidence_statement_for_t07() -> None:
    answer = "现有资料不足以回答 HKU 2026 年 QS 排名。"

    assert is_insufficient_answer(answer) is True
    assert split_claims(answer) == []


@pytest.mark.parametrize(
    "answer",
    [
        "关于 RAG 附加内容：明白，后续回答不再附带 RAG 相关内容。",
        "好的，我会按照你的要求回答。",
        "收到，后续不再重复背景知识。",
        "Understood, I will follow your requested format.",
        "本轮网页搜索已执行并返回 3 条结果，但这些结果与本问题无关。",
        "该信息来源于第 1 页。",
    ],
)
def test_split_claims_excludes_nonfactual_acknowledgements_and_meta(answer: str) -> None:
    assert is_nonfactual_claim(answer) is True
    assert split_claims(answer) == []


def test_nonfactual_filter_keeps_real_document_claims() -> None:
    answer = "讲义中出现了 Agentic AI，并把规划列为智能体组件。"

    assert is_nonfactual_claim(answer) is False
    assert split_claims(answer) == ["讲义中出现了 Agentic AI，并把规划列为智能体组件"]


def test_split_claims_excludes_markdown_headings_but_keeps_factual_bullets() -> None:
    answer = (
        "## 主要结果\n"
        "- RAG-Sequence 对整个输出序列使用同一组文档。[1]\n"
        "- RAG-Token 可以为不同 token 使用不同文档。[2]\n"
        "## 局限性\n"
    )

    assert split_claims(answer) == [
        "RAG-Sequence 对整个输出序列使用同一组文档 [1]",
        "RAG-Token 可以为不同 token 使用不同文档 [2]",
    ]


def test_split_claims_excludes_source_section_and_code_fence() -> None:
    answer = (
        "模型使用混合检索。\n"
        "```python\nresult = retrieve(query)\n```\n"
        "## 来源\n"
        "[1] rag_paper.pdf 第 3 页\n"
    )

    assert split_claims(answer) == ["模型使用混合检索"]


def test_split_claim_spans_point_back_to_original_answer() -> None:
    answer = "## 方法\n- 模型结合 BM25 和向量检索。[1]\n"

    spans = split_claim_spans(answer)

    assert len(spans) == 1
    assert spans[0].text == "模型结合 BM25 和向量检索 [1]"
    assert answer[spans[0].start : spans[0].end].startswith("模型结合 BM25")


def test_verification_context_prioritises_prompt_then_history_then_memory() -> None:
    context = build_verification_context(
        "我的研究方向现在是 Agentic RAG。",
        history=[ConversationTurn(question="旧问题", answer="旧回答", turn_index=1)],
        memory_context=["我的研究方向是传统 NLP。"],
    )

    assert [source.source_type for source in context.sources] == [
        VerificationSourceType.USER_PROMPT,
        VerificationSourceType.CONVERSATION,
        VerificationSourceType.LONG_TERM_MEMORY,
    ]
    assert all(source.academic_citation is False for source in context.sources)
    assert context.sources[0].priority > context.sources[-1].priority


def test_verification_context_ignores_blank_sources() -> None:
    context = build_verification_context("  ", memory_context=["", "偏好中文回答"])

    assert len(context.sources) == 1
    assert context.sources[0].source_id == "memory:1"


@pytest.mark.parametrize("case", _CASES, ids=[case["case_id"] for case in _CASES])
def test_fixed_verification_cases(case: dict[str, str]) -> None:
    check = LexicalVerifier().verify(case["claim"], [_evidence(case["evidence_text"])])[0]
    assert check.status.value == case["expected_status"]
    assert 0 <= check.confidence <= 1


def test_empty_evidence_marks_every_claim_unsupported() -> None:
    checks = LexicalVerifier().verify("First supported-looking claim. Second claim.", [])
    assert len(checks) == 2
    assert all(check.status == ClaimStatus.UNSUPPORTED for check in checks)
    assert all(check.supporting_evidence == [] for check in checks)
    assert all(check.confidence == 0 for check in checks)
    assert all((check.note or "").startswith("UNVERIFIED:") for check in checks)


def test_semantic_verifier_supports_chinese_claim_with_english_evidence() -> None:
    llm = _JSONLLM(_semantic_result("supported", evidence_indices=[1]))
    verifier = SemanticVerifier(llm)
    evidence = _evidence(
        "Retrieval-Augmented Generation combines external retrieved documents "
        "with a language model during generation.",
        "rag:p1:c0",
        1,
    )

    check = verifier.verify(
        "RAG 将检索到的外部文档与语言模型生成过程结合起来。[1]", [evidence]
    )[0]

    assert check.status == ClaimStatus.SUPPORTED
    assert check.supporting_evidence == ["rag:p1:c0"]
    assert verifier.last_usage.total_tokens == 42
    assert "different languages" in llm.messages[0]["content"]


def test_semantic_verifier_marks_cross_language_partial_support() -> None:
    verifier = SemanticVerifier(
        _JSONLLM(
            _semantic_result(
                "partially_supported",
                confidence=0.72,
                evidence_indices=[1],
                reason="The retrieval step is supported but the performance claim is not.",
            )
        )
    )
    evidence = _evidence("RAG retrieves external passages before generation.")

    check = verifier.verify("RAG 会先检索资料，并保证答案完全正确。[1]", [evidence])[0]

    assert check.status == ClaimStatus.PARTIALLY_SUPPORTED
    assert check.confidence == 0.72
    assert check.supporting_evidence == [evidence.chunk_id]


def test_citation_marker_does_not_allow_different_evidence() -> None:
    verifier = SemanticVerifier(
        _JSONLLM(_semantic_result("unsupported", evidence_indices=[2]))
    )
    evidence = [
        _evidence("This page discusses agent planning.", "agent:p1:c0", 1),
        _evidence("RAG retrieves external passages.", "rag:p3:c0", 3),
    ]

    check = verifier.verify("RAG 会检索外部资料。[1]", evidence)[0]

    assert check.status == ClaimStatus.UNSUPPORTED
    assert check.supporting_evidence == []


def test_semantic_verifier_supports_english_claim() -> None:
    verifier = SemanticVerifier(
        _JSONLLM(_semantic_result("supported", evidence_indices=[1]))
    )
    evidence = _evidence("RAG combines parametric and non-parametric memory.")

    check = verifier.verify(
        "RAG combines parametric and non-parametric memory. [1]", [evidence]
    )[0]

    assert check.status == ClaimStatus.SUPPORTED


def test_semantic_verifier_marks_conflicting_evidence_unverified() -> None:
    verifier = SemanticVerifier(
        _JSONLLM(
            _semantic_result(
                "unsupported",
                confidence=0.8,
                evidence_indices=[1, 2],
                can_determine=False,
                reason="The two sources contradict each other.",
            )
        )
    )
    evidence = [
        _evidence("The method always retrieves.", "a:p1:c0", 1),
        _evidence("The method retrieves only when needed.", "b:p2:c0", 2),
    ]

    check = verifier.verify("The method always retrieves.", evidence)[0]

    assert check.status == ClaimStatus.UNSUPPORTED
    assert check.confidence == 0
    assert check.supporting_evidence == []
    assert (check.note or "").startswith("UNVERIFIED:")


def test_semantic_verifier_falls_back_on_invalid_model_output() -> None:
    verifier = SemanticVerifier(_JSONLLM("not JSON"))

    check = verifier.verify(
        "RAG combines retrieved passages with a language model response.",
        [_evidence("RAG combines retrieved passages with a language model response.")],
    )[0]

    assert check.status == ClaimStatus.SUPPORTED
    assert "Semantic verification unavailable" in (check.note or "")


def test_supporting_ids_come_from_input_evidence() -> None:
    evidence = [
        _evidence("Unrelated database content.", "other:p8:c0", 8),
        _evidence(
            "RAG combines retrieved passages with a language model response.",
            "rag:p2:c0",
            2,
        ),
    ]
    check = LexicalVerifier().verify(
        "RAG combines retrieved passages with a language model response.", evidence
    )[0]
    assert check.status == ClaimStatus.SUPPORTED
    assert check.supporting_evidence[0] == "rag:p2:c0"
    assert set(check.supporting_evidence) <= {item.chunk_id for item in evidence}


def test_invalid_configuration_is_rejected() -> None:
    with pytest.raises(ValueError):
        LexicalVerifier(supported_threshold=0.2, partial_threshold=0.5)
    with pytest.raises(ValueError):
        LexicalVerifier(max_evidence=0)


def test_lexical_verifier_integrates_with_mock_b4_pipeline() -> None:
    orch = build_mock_orchestrator()
    orch.verifier = LexicalVerifier()

    state = orch.run(
        "讲义里 Self-RAG 是怎么决定要不要检索的？",
        **MODE_PRESETS["B4"],
    )

    evidence_ids = {item.chunk_id for item in state.evidence}
    assert state.claims
    assert any(check.status == ClaimStatus.SUPPORTED for check in state.claims)
    assert all(
        set(check.supporting_evidence) <= evidence_ids for check in state.claims
    )
    assert state.errors == []


def test_orchestrator_skips_direct_answer_without_document_evidence() -> None:
    orch = build_mock_orchestrator()
    state = AgentState(question="What is 2+2?", enable_verification=True)
    state.route = RouteDecision(route=RouteType.DIRECT, need_retrieval=False)
    state.draft_answer = "2+2 equals 4."

    orch._run_verification(state)

    assert state.claims == []
    skip = [event for event in state.trace if event.stage == "verify" and event.event == "skip"]
    assert skip[-1].detail["reason"] == "no_document_evidence"


def test_orchestrator_absorbs_semantic_verifier_usage() -> None:
    orch = build_mock_orchestrator()
    orch.verifier = SemanticVerifier(
        _JSONLLM(_semantic_result("supported", evidence_indices=[1]))
    )
    state = AgentState(question="What is RAG?", enable_verification=True)
    state.route = RouteDecision(
        route=RouteType.DOCUMENT_RETRIEVAL, need_retrieval=True
    )
    state.draft_answer = "RAG combines retrieval and generation. [1]"
    state.evidence = [
        _evidence("RAG combines retrieval and generation.", "rag:p1:c0", 1)
    ]

    orch._run_verification(state)

    assert state.claims[0].status == ClaimStatus.SUPPORTED
    assert state.usage.total_tokens == 42
    assert state.usage.llm_calls == 1
