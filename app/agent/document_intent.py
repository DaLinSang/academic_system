"""文档任务意图识别（方案 §3.5，A 负责）。

把用户问题分成 qa / summary / comparison，并判断是否指定单一文档。
纯函数、不依赖 LLM、不改公共契约——作为 RouteDecision 的附加判断，
避免为了这个功能改动 types.py（公共契约需三人确认）。

规则（严格按方案 §3.5）：
- "总结/概括/梳理/讲了什么/全文/overview/summarize" → summary
- "比较/对比/区别/compare/vs" → comparison
- 其它（含"RAG 有什么优势"这类含"内容/分析"的普通问题）→ qa
- 指定文件：显式 .pdf 文件名；未指定时只有"当前只选一个文件"才推断
"""

from __future__ import annotations

import re

from app.core.types import RouteType

__all__ = ["document_task", "requested_document", "should_summarize"]

_SUMMARY_RE = re.compile(
    r"(?:总结|概括|梳理|概述|全文总结|讲了什么|主要讲了|内容概要"
    r"|整理|复习提纲|提纲|归纳"
    r"|overview|summari[sz]e|by way of summary)", re.I,
)
_COMPARE_RE = re.compile(r"(?:比较|对比|区别|异同|compare|comparison|versus|\bvs\b)", re.I)
_PDF_RE = re.compile(r"[\w.-]+\.pdf\b", re.I)


def document_task(question: str, route: RouteType | None = None) -> str:
    """返回 'qa' | 'summary' | 'comparison'。"""
    if _COMPARE_RE.search(question):
        return "comparison"
    if _SUMMARY_RE.search(question):
        return "summary"
    return "qa"


def requested_document(question: str, indexed_names: list[str] | None = None) -> str | None:
    """返回用户明确指定的单个文档文件名；无法唯一确定时返回 None。

    - 显式 .pdf 文件名优先；
    - 没有显式文件名、但仅有一个已索引文档且问题是总结类，才推断为该文件。
    """
    names = _PDF_RE.findall(question)
    if len(names) == 1:
        return names[0]
    if names:  # 多个文件 → comparison，不该走单篇全文
        return None
    if indexed_names and len(indexed_names) == 1 and document_task(question) == "summary":
        return indexed_names[0]
    return None


def should_summarize(
    question: str,
    indexed_names: list[str] | None = None,
    route: RouteType | None = None,
) -> bool:
    """只有 summary 意图 + 唯一目标文档 + 非 comparison 才走全文总结。"""
    if document_task(question, route) != "summary":
        return False
    return requested_document(question, indexed_names) is not None
