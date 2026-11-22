"""Recognise study requests and put representative sources within the answer budget."""

from __future__ import annotations

import re

from app.core.types import Evidence

_STUDY = re.compile(
    r"总结|概括|综述|都讲了?什|讲了?些什|讲了?什么|讲了哪些|有哪些内容|"
    r"主要内容|核心内容|全面介绍|整体介绍|复习|提纲|知识点|学习笔记|"
    r"研究目标.*(?:方法|结论|结果)|研究目的.*(?:方法|结论|结果)|"
    r"比较|对比|异同|相同点|不同点|"
    r"summari[sz]|overview|what (?:does|do) .{0,50} cover|"
    r"key points|study guide|revision notes|compare|comparison|contrast",
    re.I,
)
_SPECIFIC_PAGE = re.compile(r"第\s*\d+\s*页|\bpage\s+\d+\b", re.I)


def is_study_request(query: str) -> bool:
    """Broad coverage, revision and comparison requests; not exact fact lookup."""
    return bool(_STUDY.search(query)) and not bool(_SPECIFIC_PAGE.search(query))


def prioritize_study_evidence(evidence: list[Evidence]) -> list[Evidence]:
    """Place one distinct page from each document in each round, keeping the tail.

    AnswerGenerator sees only the first six evidence blocks.  The remaining
    evidence stays in state for UI and diagnostics, but cannot crowd those six.
    """
    by_source: dict[str, list[Evidence]] = {}
    for item in evidence:
        by_source.setdefault(item.file_name, []).append(item)
    for items in by_source.values():
        seen_pages: set[int] = set()
        unique: list[Evidence] = []
        repeated: list[Evidence] = []
        for item in items:
            if item.page_number in seen_pages:
                repeated.append(item)
            else:
                unique.append(item)
                seen_pages.add(item.page_number)
        items[:] = unique + repeated
    ordered: list[Evidence] = []
    for rank in range(max((len(items) for items in by_source.values()), default=0)):
        ordered.extend(items[rank] for items in by_source.values() if rank < len(items))
    return ordered
