"""路由降级路径的健壮性与 need_memory 语义。

Issue #25：B 的三份 Demo PDF 都是英文，无 key 时路由器走关键词降级，
此前只认中文关键词，导致英文文档问题被判成 direct、检索根本不触发。

本文件同时钉死一个容易误解的语义：
`need_memory` 只是路由对"已有记忆是否有帮助"的判断，
**不是**是否读取记忆的开关（那是 enable_memory / B1–B4 的事）。
"""

from __future__ import annotations

import json

import pytest

from app.core.types import LLMResponse, RouteType, Usage
from app.routing.router import LLMRouter, RuleBasedRouter


# --------------------------------------------------------------------------- #
# Issue #25：英文降级必须触发检索
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "question",
    [
        "What problem does Retrieval-Augmented Generation solve?",  # Issue #25 原句
        "What problem does this paper solve?",
        "According to the lecture, how does RAG work?",
        "In the PDF, which page explains BM25?",
        "What are the limitations described in the uploaded document?",
        "What does the attached paper say about memory?",
    ],
)
def test_english_document_questions_trigger_retrieval(question):
    route = RuleBasedRouter().route(question, has_documents=True)
    assert route.need_retrieval is True, f"英文文档问题未触发检索: {question}"
    assert route.route is RouteType.DOCUMENT_RETRIEVAL


@pytest.mark.parametrize(
    "question",
    [
        "Compare RAG and ReAct in the uploaded papers",
        "Compare the two documents and write a report",
        "Literature review of retrieval augmented generation",
        "Summarize these several documents",
    ],
)
def test_english_complex_questions_are_planned(question):
    route = RuleBasedRouter().route(question, has_documents=True)
    assert route.route is RouteType.COMPLEX_TASK
    assert route.need_planning is True
    assert route.need_retrieval is True


def test_case_insensitive_matching():
    upper = RuleBasedRouter().route("WHAT PROBLEM DOES THIS PAPER SOLVE?", True)
    lower = RuleBasedRouter().route("what problem does this paper solve?", True)
    assert upper.route == lower.route
    assert upper.need_retrieval == lower.need_retrieval


# --------------------------------------------------------------------------- #
# 不能因为加了英文关键词就过度检索
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "question",
    [
        "What is attention?",
        "What is the weather today?",
        "Translate this sentence into English",
        "Hello, how are you?",
    ],
)
def test_generic_english_questions_stay_direct(question):
    """RQ1 的"不必要检索率"依赖这条：泛化问句不能被算成需要检索。"""
    route = RuleBasedRouter().route(question, has_documents=True)
    assert route.route is RouteType.DIRECT
    assert route.need_retrieval is False


def test_chinese_behavior_unchanged():
    route = RuleBasedRouter().route("这篇论文解决了什么问题？", has_documents=True)
    assert route.route is RouteType.DOCUMENT_RETRIEVAL
    assert route.need_retrieval is True

    generic = RuleBasedRouter().route("什么是 attention", has_documents=True)
    assert generic.route is RouteType.DIRECT


def test_no_documents_means_no_retrieval():
    route = RuleBasedRouter().route("What problem does this paper solve?", has_documents=False)
    assert route.need_retrieval is False, "没有文档时不该检索"


# --------------------------------------------------------------------------- #
# need_memory：能被模型正确解析，但不是读取开关
# --------------------------------------------------------------------------- #
class ScriptedLLM:
    def __init__(self, payload: dict) -> None:
        self.text = "好的：\n```json\n" + json.dumps(payload) + "\n```"
        self.calls = 0

    def generate(self, messages, temperature=0.0, max_tokens=None, json_mode=False):
        self.calls += 1
        return LLMResponse(
            text=self.text,
            model="scripted",
            usage=Usage(total_tokens=30, llm_calls=1),
        )


def _payload(**overrides) -> dict:
    data = {
        "route": "document_retrieval",
        "need_retrieval": True,
        "need_memory": False,
        "need_planning": False,
        "confidence": 0.8,
        "reason": "指向用户文档",
    }
    data.update(overrides)
    return data


def test_need_memory_true_is_parsed():
    router = LLMRouter(ScriptedLLM(_payload(need_memory=True)))
    route = router.route("What does my note say?", has_documents=True)
    assert route.need_memory is True


def test_need_memory_false_is_parsed():
    router = LLMRouter(ScriptedLLM(_payload(need_memory=False)))
    route = router.route("What does my note say?", has_documents=True)
    assert route.need_memory is False


def test_need_memory_does_not_gate_retrieval():
    """need_memory 只影响生成阶段是否参考记忆，不影响检索。"""
    router = LLMRouter(ScriptedLLM(_payload(need_memory=False)))
    route = router.route("Compare the two papers", has_documents=True)
    assert route.need_retrieval is True


def test_system_prompt_clarifies_need_memory_semantics():
    """提示词必须说明读取开关是 enable_memory，否则模型会一直填 false。"""
    from app.routing.router import _SYSTEM_PROMPT

    assert "enable_memory" in _SYSTEM_PROMPT
    assert "是否**读取**记忆" in _SYSTEM_PROMPT
