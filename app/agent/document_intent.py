"""文档任务意图识别（方案 §3.5 / §4.4，A 负责）。

把用户问题分成 qa / summary / comparison，并确定目标文档。
纯函数、不依赖 LLM、不改 RouteType——作为 RouteDecision 的附加判断。

目标文档选择顺序（方案 §4.4）：
1. 问题里显式写了 PDF 文件名 → 用它；
2. "这个文档/这篇论文/该文件" → 用 active_document_file_name；
3. 没有指代但只有一份已索引 PDF 且是总结类问题 → 用唯一文件；
4. 多份 PDF 且无法判断 → 返回 AMBIGUOUS，要求用户选择，绝不猜。
"""

from __future__ import annotations

import re

from app.core.types import RouteType

__all__ = [
    "document_task", "requested_document", "should_summarize", "AMBIGUOUS",
]

AMBIGUOUS = "__AMBIGUOUS__"

_SUMMARY_RE = re.compile(
    r"(?:总结|概括|梳理|概述|全文总结|讲了什么|主要讲了|内容概要"
    r"|讲讲|讲一下|讲一讲|说一说|说说|介绍一下|介绍下|讲讲这篇"
    r"|整理|复习提纲|提纲|归纳|通读|通篇"
    r"|overview|summari[sz]e|walk me through|tell me about|by way of summary)", re.I,
)
_COMPARE_RE = re.compile(r"(?:比较|对比|区别|异同|compare|comparison|versus|\bvs\b)", re.I)
_PDF_RE = re.compile(r"[\w.-]+\.pdf\b", re.I)
_DEICTIC_RE = re.compile(
    r"(?:这个文档|这份文档|这篇论文|这篇文章|这篇文档|这个文件|这份材料"
    r"|该文件|本文件|上面这个|刚才那个"
    r"|this document|this paper|this article|this file|it)", re.I
)


def document_task(question: str, route: RouteType | None = None) -> str:
    """返回 'qa' | 'summary' | 'comparison'。"""
    if _COMPARE_RE.search(question):
        return "comparison"
    if _SUMMARY_RE.search(question):
        return "summary"
    return "qa"


def requested_document(
    question: str,
    indexed_names: list[str] | None = None,
    active_document_file_name: str | None = None,
) -> str | None:
    """返回唯一目标文档；多份无法判断返回 AMBIGUOUS；无需文档返回 None。

    indexed_names 与 active_document_file_name 由调用方（Orchestrator）
    从 retriever 和 AgentState 传入，本函数不访问外部状态。
    """
    # 1) 显式文件名（最高优先级）
    names = _PDF_RE.findall(question)
    if len(names) == 1:
        return names[0]
    if len(names) > 1:
        return AMBIGUOUS

    # 2) 指代词 → 当前会话文件；多份且无当前文件时不能猜
    if _DEICTIC_RE.search(question):
        if active_document_file_name:
            return active_document_file_name
        if indexed_names and len(indexed_names) == 1:
            return indexed_names[0]
        if indexed_names and len(indexed_names) > 1:
            return AMBIGUOUS
        return None

    # 3) 总结类问题 + 唯一索引文件才推断；多份要求选择
    if document_task(question) == "summary":
        if indexed_names and len(indexed_names) == 1:
            return indexed_names[0]
        if indexed_names and len(indexed_names) > 1:
            return AMBIGUOUS
    return None


def should_summarize(
    question: str,
    indexed_names: list[str] | None = None,
    active_document_file_name: str | None = None,
    route: RouteType | None = None,
) -> bool:
    """只有 summary 意图 + 唯一目标文档才走全文总结。"""
    if document_task(question, route) != "summary":
        return False
    target = requested_document(question, indexed_names, active_document_file_name)
    return target is not None and target != AMBIGUOUS
