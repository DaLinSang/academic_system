from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agent.factory import build_mock_orchestrator
from app.agent.orchestrator import MODE_PRESETS
from app.core.interfaces import Verifier
from app.core.types import ClaimStatus, Evidence, RetrievalMethod
from app.verification import LexicalVerifier, split_claims

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
