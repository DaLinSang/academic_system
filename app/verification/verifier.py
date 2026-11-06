"""Stage 1 deterministic claim verifier.

The implementation intentionally uses no external LLM, database, or retrieval
service. It consumes the frozen core Evidence type and satisfies the Verifier
protocol, so Member C can develop and test it against Stage 0 mocks.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from app.core.types import ClaimCheck, ClaimStatus, Evidence

_SENTENCE_SPLIT = re.compile(r"[。！？!?；;\n]+|(?<=[.])\s+(?=[A-Z0-9])")
_CITATION = re.compile(r"\[\d+\]")
_PAGE_LOCATOR = re.compile(r"[（(][^）)]*第\s*\d+\s*页[^）)]*[）)]")
_LIST_PREFIX = re.compile(r"^(?:[-•*·]+|\d+[.)、])\s*")
_META = re.compile(
    r"^(?:根据|依据|以上|综上|总之|下面|如下|参考|注[:：]|说明[:：]|"
    r"in summary|according to|based on|the following)",
    re.IGNORECASE,
)
_ENGLISH_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "in", "is", "it", "of", "on", "or", "that", "the", "this", "to",
    "was", "were", "with",
}


def _substantive_text(text: str) -> str:
    text = _CITATION.sub("", text)
    text = _PAGE_LOCATOR.sub("", text)
    return re.sub(r"\s+", " ", text).strip(" -•*·\t")


def split_claims(answer: str, *, min_length: int = 8) -> list[str]:
    """Split an answer into factual sentence-like claims.

    Introductory/meta sentences, pure citation locators, and very short fragments
    are excluded because treating them as claims inflates the unsupported rate.
    """
    claims: list[str] = []
    for raw in _SENTENCE_SPLIT.split(answer.strip()):
        candidate = _LIST_PREFIX.sub("", raw.strip())
        substantive = _substantive_text(candidate)
        if not substantive or len(substantive) < min_length:
            continue
        if _META.match(substantive):
            continue
        claims.append(candidate)
    return claims


def _tokens(text: str) -> set[str]:
    clean = _substantive_text(text).lower()
    english = {
        word
        for word in re.findall(r"[a-z0-9]+", clean)
        if len(word) > 1 and word not in _ENGLISH_STOPWORDS
    }
    chinese_runs = re.findall(r"[\u4e00-\u9fff]+", clean)
    chinese: set[str] = set()
    for run in chinese_runs:
        if len(run) == 1:
            chinese.add(run)
        else:
            chinese.update(run[index : index + 2] for index in range(len(run) - 1))
    return english | chinese


class LexicalVerifier:
    """Rank Evidence by claim-token coverage and assign three support levels."""

    def __init__(
        self,
        supported_threshold: float = 0.62,
        partial_threshold: float = 0.40,
        max_evidence: int = 3,
    ) -> None:
        if not 0 <= partial_threshold <= supported_threshold <= 1:
            raise ValueError("thresholds must satisfy 0 <= partial <= supported <= 1")
        if max_evidence < 1:
            raise ValueError("max_evidence must be at least 1")
        self.supported_threshold = supported_threshold
        self.partial_threshold = partial_threshold
        self.max_evidence = max_evidence

    @staticmethod
    def score(claim: str, evidence_text: str) -> float:
        claim_tokens = _tokens(claim)
        if not claim_tokens:
            return 0.0
        return len(claim_tokens & _tokens(evidence_text)) / len(claim_tokens)

    def _verify_claim(self, claim: str, evidence: list[Evidence]) -> ClaimCheck:
        ranked = sorted(
            ((self.score(claim, item.text), item) for item in evidence),
            key=lambda pair: pair[0],
            reverse=True,
        )
        best_score = ranked[0][0] if ranked else 0.0
        matched_ids = [
            item.chunk_id
            for score, item in ranked[: self.max_evidence]
            if score > 0
        ]

        if best_score >= self.supported_threshold:
            return ClaimCheck(
                claim=claim,
                status=ClaimStatus.SUPPORTED,
                confidence=round(0.5 + best_score / 2, 4),
                supporting_evidence=matched_ids,
            )
        if best_score >= self.partial_threshold:
            return ClaimCheck(
                claim=claim,
                status=ClaimStatus.PARTIALLY_SUPPORTED,
                confidence=round(0.5 + abs(best_score - 0.5), 4),
                supporting_evidence=matched_ids,
                note="Evidence covers only part of the claim.",
            )
        return ClaimCheck(
            claim=claim,
            status=ClaimStatus.UNSUPPORTED,
            confidence=round(1.0 - best_score, 4),
            supporting_evidence=[],
            note="Retrieved evidence does not sufficiently support this claim.",
        )

    def verify(self, answer: str, evidence: list[Evidence]) -> list[ClaimCheck]:
        """Verify all claims and degrade safely if internal processing fails."""
        claims = split_claims(answer)
        if not claims:
            return []
        if not evidence:
            return [
                ClaimCheck(
                    claim=claim,
                    status=ClaimStatus.UNSUPPORTED,
                    confidence=1.0,
                    note="No retrieval evidence is available.",
                )
                for claim in claims
            ]
        try:
            return [self._verify_claim(claim, evidence) for claim in claims]
        except Exception as exc:  # noqa: BLE001 - protocol requires graceful degradation
            return [
                ClaimCheck(
                    claim=claim,
                    status=ClaimStatus.UNSUPPORTED,
                    confidence=0.0,
                    note=f"Verification degraded after {type(exc).__name__}.",
                )
                for claim in claims
            ]

    def verify_evidence(self, answer: str, evidence: Iterable[Evidence]) -> list[ClaimCheck]:
        """Convenience wrapper for callers that hold any Evidence iterable."""
        return self.verify(answer, list(evidence))
