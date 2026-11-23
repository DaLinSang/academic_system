"""Issue #99：Planner 检索子查询继承 Chapter 章节约束。

守：
1. Chapter9 / Chapter 2 等变体在子查询丢失时补回
2. 多个 Chapter 对比时全部补回（Chapter9 + Chapter10）
3. 子查询已带章节号时不重复补
4. 无章节指代的问题不受影响（#64 的 lecture/文件名继承不回归）
5. 与 B 的 document_scope Chapter 识别格式兼容
"""

from __future__ import annotations

from app.agent.orchestrator import Orchestrator
from app.core.types import AgentState


def state_with(question: str) -> AgentState:
    return AgentState(question=question)


def test_chapter_constraint_inherited_into_subquery():
    """Issue #99 原始现象：子查询 'Apriori FP-Growth...' 丢了 Chapter9。"""
    s = state_with("根据 Chapter9-AssociationRule 整理一份复习提纲")
    out = Orchestrator._enrich_query("Apriori FP-Growth 算法 流程 剪枝 示例", s)
    assert "Chapter9" in out
    assert "AssociationRule" in out
    assert "Apriori" in out


def test_chapter_with_space_variant():
    s = state_with("请总结 Chapter 2 Association Rule 的内容")
    out = Orchestrator._enrich_query("支持度 置信度 定义", s)
    assert "Chapter 2" in out
    assert "Association Rule" in out


def test_multiple_chapters_all_inherited():
    """对比类问题：Chapter9 和 Chapter10 都要补回，不能只补第一个。"""
    s = state_with("请总结 Chapter9-AssociationRule 和 Chapter10 的区别")
    out = Orchestrator._enrich_query("关联规则 与 网络", s)
    assert "Chapter9" in out and "Chapter10" in out


def test_no_duplicate_chapter():
    """子查询已带章节号时不重复注入。"""
    s = state_with("根据 Chapter9 整理复习提纲")
    out = Orchestrator._enrich_query("Chapter9 关联规则 定义", s)
    assert out.lower().count("chapter9") == 1


def test_no_chapter_question_unchanged():
    """无章节指代：不凭空补 Chapter。"""
    s = state_with("请总结 rag_paper.pdf 的研究目标")
    out = Orchestrator._enrich_query("研究目标 方法", s)
    assert "Chapter" not in out


def test_lecture_and_filename_still_inherited():
    """#64/#72 回归：lecture 编号与 PDF 文件名仍正常补回。"""
    s = state_with("总结 rag_paper.pdf 里 Lecture 6 的 memory")
    out = Orchestrator._enrich_query("memory 是什么", s)
    assert "rag_paper.pdf" in out
    assert "Lecture 6" in out


def test_chapter_compatible_with_b_scope_regex():
    """补回的格式必须能被 B 的 document_scope._CHAPTER 识别。"""
    from app.retrieval.document_scope import _CHAPTER
    s = state_with("根据 Chapter9-AssociationRule 整理复习提纲")
    out = Orchestrator._enrich_query("Apriori 算法", s)
    match = _CHAPTER.search(out)
    assert match is not None
    assert int(match.group(1)) == 9
