"""Stage 1 deterministic claim verifier.

The implementation intentionally uses no external LLM, database, or retrieval
service. It consumes the frozen core Evidence type and satisfies the Verifier
protocol, so Member C can develop and test it against Stage 0 mocks.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app.core.interfaces import LLM
from app.core.types import ClaimCheck, ClaimStatus, Evidence, Usage

_SENTENCE_SPLIT = re.compile(r"[。！？!?\n]+|(?<=[.])\s+(?=[A-Z0-9])")
_CITATION = re.compile(r"\[(\d+)\]")
_PAGE_LOCATOR = re.compile(r"[（(][^）)]*第\s*\d+\s*页[^）)]*[）)]")
_LIST_PREFIX = re.compile(r"^(?:[-•*·]+|\d+[.)、])\s*")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+")
_SECTION_LABEL = re.compile(
    r"^(?:文档概述|研究问题|背景|目标|方法|主要内容|主要结果|实验结果|"
    r"局限性|局限|结论|总结|来源|来源引用|参考资料|参考文献|references?)\s*[:：]?$",
    re.IGNORECASE,
)
_SOURCE_LINE = re.compile(
    r"^(?:来源|参考资料|参考文献|references?)\s*[:：]|"
    r"^\[\d+\]\s*(?:https?://|[^。！？!?]{0,120}(?:p\.|第\s*\d+\s*页))",
    re.IGNORECASE,
)
_TABLE_SEPARATOR = re.compile(r"^\|?(?:\s*:?-{3,}:?\s*\|)+\s*$")
_META = re.compile(
    r"^(?:根据|依据|以上|综上|总之|下面|如下|参考|注[:：]|说明[:：]|"
    r"in summary|according to|based on|the following)",
    re.IGNORECASE,
)
_INSUFFICIENT_CLAIM = re.compile(
    r"^(?:(?:由于|因为)\s*)?"
    r"(?:(?:现有|当前|已上传|所提供|检索到的)\s*)?"
    r"(?:资料|材料|文档|证据|信息).{0,20}"
    r"(?:不足以|不足.{0,8}无法|未包含|没有提到|无法支持|不能支持|无法回答)"
    r"|^(?:我|系统)?\s*(?:无法|不能).{0,20}(?:回答|确定|核实)"
    r"|^(?:未|没有)(?:在.{0,20})?找到.{0,20}(?:相关|支持).{0,20}"
    r"(?:证据|内容|信息)"
    r"|^(?:the\s+)?(?:available|provided|retrieved)?\s*"
    r"(?:evidence|documents?|information).{0,30}"
    r"(?:is insufficient|does not contain|cannot support)"
    r"|^(?:i\s+)?cannot.{0,20}(?:answer|determine|verify)",
    re.IGNORECASE,
)
_NON_FACTUAL_CLAIM = re.compile(
    r"^(?:关于[^：:\n]{0,40}[：:]\s*)?"
    r"(?:明白|好的|好|收到|了解|没问题|可以|当然|遵命|"
    r"我(?:会|将|不会|不再|可以|会按|将按)|"
    r"后续(?:会|将|不会|不再)|接下来(?:会|将|不会|不再)|"
    r"按(?:照)?(?:你的|您(?:的)?|用户的)?要求|"
    r"understood|got it|okay|sure|i (?:will|won't|will not)|"
    r"from now on i (?:will|won't|will not))"
    r"|^(?:本轮|此次|当前轮次).{0,20}(?:网页搜索|联网搜索|检索)"
    r".{0,30}(?:已执行|已完成|返回\s*\d+\s*条|没有可作为证据)"
    r"|^(?:该|此|上述)?(?:信息|内容|回答|结论).{0,12}"
    r"(?:来源于|来自|引自)\s*(?:文档|材料)?\s*第?\s*\d+\s*页",
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


def is_insufficient_claim(text: str) -> bool:
    """Return True for refusal/absence statements, not factual document claims."""
    return bool(_INSUFFICIENT_CLAIM.search(_substantive_text(text)))


def is_insufficient_answer(answer: str) -> bool:
    """Detect answers whose main result is that uploaded evidence is insufficient."""
    return any(is_insufficient_claim(part) for part in _SENTENCE_SPLIT.split(answer))


def is_nonfactual_claim(text: str) -> bool:
    """Return True for acknowledgements, promises, and system meta narration.

    These sentences describe the assistant's intended behaviour or execution trace,
    rather than a fact that should be supported by retrieved document evidence.
    """
    return bool(_NON_FACTUAL_CLAIM.search(_substantive_text(text)))


@dataclass(frozen=True, slots=True)
class ClaimSpan:
    """A factual claim and its approximate location in the original answer."""

    text: str
    start: int
    end: int


def _candidate_lines(answer: str) -> Iterable[tuple[str, int, int]]:
    """Yield answer regions that may contain claims, excluding Markdown chrome."""
    offset = 0
    in_code_fence = False
    in_sources = False
    for raw_line in answer.splitlines(keepends=True):
        line = raw_line.rstrip("\r\n")
        stripped = line.strip()
        start = offset
        offset += len(raw_line)
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_code_fence = not in_code_fence
            continue
        if in_code_fence or not stripped:
            continue
        if _HEADING.match(line):
            in_sources = bool(_SECTION_LABEL.fullmatch(_HEADING.sub("", line).strip())) and bool(
                re.search(r"来源|参考|reference", line, re.IGNORECASE)
            )
            continue
        if _SECTION_LABEL.fullmatch(stripped):
            in_sources = bool(re.search(r"来源|参考|reference", stripped, re.IGNORECASE))
            continue
        if in_sources or _SOURCE_LINE.match(stripped) or _TABLE_SEPARATOR.match(stripped):
            continue
        # A Markdown table header is navigation, not a factual row. Keep later rows
        # only when they contain sentence-like content; the separator itself was removed above.
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if all(len(cell) < 12 and not re.search(r"[。！？!?]$", cell) for cell in cells):
                continue
            stripped = "；".join(cell for cell in cells if cell)
        content_start = start + line.find(stripped)
        yield stripped, content_start, content_start + len(stripped)


def split_claim_spans(answer: str, *, min_length: int = 8) -> list[ClaimSpan]:
    """Split an answer into factual sentence-like claims.

    Markdown headings, section labels, source lists, code fences, introductory/meta
    sentences and very short fragments are excluded. Factual bullet items remain.
    """
    claims: list[ClaimSpan] = []
    for region, region_start, _ in _candidate_lines(answer):
        cursor = 0
        for match in _SENTENCE_SPLIT.finditer(region + "\n"):
            raw = region[cursor:match.start()]
            raw_start = region_start + cursor
            cursor = match.end()
            stripped = raw.strip()
            prefix_match = _LIST_PREFIX.match(stripped)
            candidate = _LIST_PREFIX.sub("", stripped).strip()
            candidate_start = raw_start + raw.find(stripped)
            if prefix_match:
                candidate_start += prefix_match.end()
            if _CITATION.fullmatch(candidate) and claims:
                previous = claims[-1]
                claims[-1] = ClaimSpan(
                    text=f"{previous.text} {candidate}",
                    start=previous.start,
                    end=candidate_start + len(candidate),
                )
                continue
            substantive = _substantive_text(candidate)
            if not substantive or len(substantive) < min_length:
                continue
            if _META.match(substantive):
                continue
            if is_insufficient_claim(substantive) or is_nonfactual_claim(substantive):
                continue
            claims.append(
                ClaimSpan(
                    text=candidate,
                    start=candidate_start,
                    end=candidate_start + len(candidate),
                )
            )
    return claims


def split_claims(answer: str, *, min_length: int = 8) -> list[str]:
    """Compatibility wrapper returning only claim text."""
    return [span.text for span in split_claim_spans(answer, min_length=min_length)]


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
            confidence=round(best_score, 4),
            supporting_evidence=[],
            note="LEXICAL_SCORE: Retrieved evidence does not sufficiently support this claim.",
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
                    confidence=0.0,
                    note="UNVERIFIED: No retrieval evidence is available.",
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


_SEMANTIC_SYSTEM_PROMPT = """You verify whether evidence supports factual claims.

The claim and evidence may use different languages. Judge meaning, not word overlap.
Return JSON only with this shape:
{"results":[{"claim_index":0,"status":"supported|partially_supported|unsupported","confidence":0.0,"evidence_indices":[1],"can_determine":true,"reason":"short explanation"}]}

Rules:
1. supported: the cited evidence clearly entails the whole claim.
2. partially_supported: the evidence supports only part of the claim.
3. unsupported: the evidence contradicts the claim or does not contain enough information.
4. If the available evidence is ambiguous, mutually contradictory, or insufficient for a reliable judgment, set can_determine=false. Do not pretend certainty.
5. evidence_indices are 1-based indices from the supplied evidence and must include only evidence that actually supports the judgment.
6. A citation marker existing in the claim is not proof. Check the cited evidence content.
7. Preserve claim_index exactly and return one result per claim."""


def _json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.I)
    value = json.loads(cleaned)
    if not isinstance(value, dict):
        raise ValueError("semantic verifier response must be a JSON object")
    return value


class SemanticVerifier:
    """Cross-language evidence verifier using the shared LLM interface.

    LexicalVerifier remains the deterministic experiment baseline. This verifier
    is the production implementation for Real mode and falls back safely when the
    model cannot return a valid structured judgment.
    """

    def __init__(
        self,
        llm: LLM,
        *,
        fallback: LexicalVerifier | None = None,
        max_evidence: int = 6,
        max_chars_per_evidence: int = 1200,
    ) -> None:
        if max_evidence < 1 or max_chars_per_evidence < 100:
            raise ValueError("semantic verifier limits are too small")
        self.llm = llm
        self.fallback = fallback or LexicalVerifier()
        self.max_evidence = max_evidence
        self.max_chars_per_evidence = max_chars_per_evidence
        self.last_usage = Usage()

    @staticmethod
    def _cited_indices(claim: str, evidence_count: int) -> list[int]:
        indices = []
        for raw in _CITATION.findall(claim):
            index = int(raw)
            if 1 <= index <= evidence_count and index not in indices:
                indices.append(index)
        return indices

    def _prompt(self, claims: list[str], evidence: list[Evidence]) -> str:
        evidence_blocks = []
        for index, item in enumerate(evidence, 1):
            text = re.sub(r"\s+", " ", item.text).strip()
            evidence_blocks.append(
                f"[{index}] chunk_id={item.chunk_id}; source={item.file_name}; "
                f"page={item.page_number}\n{text[: self.max_chars_per_evidence]}"
            )
        claim_blocks = []
        for index, claim in enumerate(claims):
            cited = self._cited_indices(claim, len(evidence))
            scope = cited or list(range(1, len(evidence) + 1))
            claim_blocks.append(
                f"Claim {index} (evaluate only evidence indices {scope}): {claim}"
            )
        return "Claims:\n" + "\n".join(claim_blocks) + "\n\nEvidence:\n" + "\n\n".join(evidence_blocks)

    def _parse(
        self, payload: dict[str, Any], claims: list[str], evidence: list[Evidence]
    ) -> list[ClaimCheck]:
        raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            raise ValueError("semantic verifier response has no results list")
        by_index = {
            item.get("claim_index"): item for item in raw_results if isinstance(item, dict)
        }
        checks = []
        for claim_index, claim in enumerate(claims):
            item = by_index.get(claim_index)
            if not isinstance(item, dict):
                raise ValueError(f"missing semantic result for claim {claim_index}")
            status = ClaimStatus(str(item.get("status")))
            confidence = min(1.0, max(0.0, float(item.get("confidence", 0.0))))
            can_determine = bool(item.get("can_determine", True))
            cited = self._cited_indices(claim, len(evidence))
            allowed = set(cited or range(1, len(evidence) + 1))
            indices = []
            for value in item.get("evidence_indices", []):
                if isinstance(value, int) and value in allowed and value not in indices:
                    indices.append(value)
            supporting_ids = [evidence[index - 1].chunk_id for index in indices]
            reason = str(item.get("reason", "")).strip()
            if status == ClaimStatus.UNSUPPORTED:
                supporting_ids = []
            elif not supporting_ids:
                can_determine = False
                reason = reason or "No valid supporting evidence index was returned."
            if not can_determine:
                status = ClaimStatus.UNSUPPORTED
                supporting_ids = []
                confidence = 0.0
                reason = f"UNVERIFIED: {reason or 'The available evidence is inconclusive.'}"
            checks.append(
                ClaimCheck(
                    claim=claim,
                    status=status,
                    confidence=round(confidence, 4),
                    supporting_evidence=supporting_ids,
                    note=reason or None,
                )
            )
        return checks

    def verify(self, answer: str, evidence: list[Evidence]) -> list[ClaimCheck]:
        self.last_usage = Usage()
        claims = split_claims(answer)
        if not claims:
            return []
        if not evidence:
            return [
                ClaimCheck(
                    claim=claim,
                    status=ClaimStatus.UNSUPPORTED,
                    confidence=0.0,
                    note="UNVERIFIED: No retrieval evidence is available.",
                )
                for claim in claims
            ]
        scoped_evidence = evidence[: self.max_evidence]
        try:
            response = self.llm.generate(
                [
                    {"role": "system", "content": _SEMANTIC_SYSTEM_PROMPT},
                    {"role": "user", "content": self._prompt(claims, scoped_evidence)},
                ],
                temperature=0.0,
                json_mode=True,
            )
            self.last_usage = response.usage
            return self._parse(_json_object(response.text), claims, scoped_evidence)
        except Exception as exc:  # noqa: BLE001 - deterministic fallback keeps the answer usable
            checks = self.fallback.verify(answer, scoped_evidence)
            for check in checks:
                prefix = f"Semantic verification unavailable ({type(exc).__name__}); "
                check.note = prefix + (check.note or "lexical fallback used")
            return checks
