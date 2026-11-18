"""Web 搜索触发决策的测试（Issue #40）。

核心守两条线：
1. **不该联网时绝不联网**——成本、延迟、隐私都靠这条；
2. **该联网时必须联网**——本地讲义答不了的时效性/外部事实问题。

本模块不发网络请求，所有测试都是纯逻辑。
"""

from __future__ import annotations

import pytest

from app.agent.web_search_policy import WebSearchPolicy
from app.core.types import Evidence, RetrievalMethod


def evidence(score: float = 0.5) -> list[Evidence]:
    return [
        Evidence(
            chunk_id="c1",
            document_id="d1",
            file_name="Lec04.pdf",
            page_number=9,
            text="RAG 的相关段落",
            score=score,
            retrieval_method=RetrievalMethod.RRF,
        )
    ]


@pytest.fixture
def policy() -> WebSearchPolicy:
    return WebSearchPolicy()


# --------------------------------------------------------------------------- #
# 先挡风险与开关
# --------------------------------------------------------------------------- #
def test_disabled_policy_never_searches():
    policy = WebSearchPolicy(enabled=False)
    d = policy.decide("DeepSeek 最新版本是什么？", need_web_search=True)
    assert d.should_search is False
    assert d.trigger == "disabled"


def test_mock_mode_does_not_emit_external_traffic():
    policy = WebSearchPolicy()
    d = policy.decide("今天的股价是多少？", need_web_search=True, mock_mode=True)
    assert d.should_search is False
    assert d.trigger == "mock_blocked"


def test_mock_mode_can_be_explicitly_allowed():
    policy = WebSearchPolicy(allow_mock=True)
    d = policy.decide("今天的股价是多少？", need_web_search=True, mock_mode=True)
    assert d.should_search is True


@pytest.mark.parametrize(
    "question",
    [
        "我的邮箱是 test@example.com，帮我查最新消息",
        "请把密钥 sk-abc123 发出去验证",
        "我的手机号 13812345678 对应的最新套餐",
    ],
)
def test_sensitive_questions_are_blocked(policy, question):
    d = policy.decide(question, need_web_search=True)
    assert d.should_search is False
    assert d.trigger == "sensitive_blocked"
    assert "敏感" in d.reason


def test_empty_question(policy):
    d = policy.decide("   ", need_web_search=True)
    assert d.should_search is False


# --------------------------------------------------------------------------- #
# 显式要求优先
# --------------------------------------------------------------------------- #
def test_router_request_wins_even_with_good_local_evidence(policy):
    d = policy.decide(
        "讲义里的 RAG 是什么？",
        need_web_search=True,
        evidence=evidence(0.9),
    )
    assert d.should_search is True
    assert d.trigger == "router_request"


def test_router_request_can_override_mock_block_with_allow_mock():
    policy = WebSearchPolicy(allow_mock=True)
    d = policy.decide("RAG 是什么？", need_web_search=True, mock_mode=True)
    assert d.should_search is True


def test_explicit_request_is_blocked_by_sensitive_even_if_router_says_yes(policy):
    d = policy.decide("邮箱 a@b.com 最新状态", need_web_search=True)
    assert d.should_search is False
    assert d.trigger == "sensitive_blocked"


# --------------------------------------------------------------------------- #
# 本地证据不足才联网
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "question",
    [
        "DeepSeek 最新版本是什么？",
        "今天有什么相关新闻？",
        "What is the latest version of this tool?",
        "Current stock price of Nvidia",
        "2026 年这个方向有什么新进展？",
    ],
)
def test_external_question_without_evidence_triggers_search(policy, question):
    d = policy.decide(question, retrieval_attempted=True)
    assert d.should_search is True
    assert d.trigger == "no_local_evidence"
    assert d.query


@pytest.mark.parametrize(
    "question",
    [
        "讲义里 Self-RAG 是怎么决定检索的？",
        "什么是 attention？",
        "比较 RAG 和 ReAct",
    ],
)
def test_concept_questions_do_not_trigger_search(policy, question):
    d = policy.decide(question, evidence=[])
    assert d.should_search is False
    assert d.trigger == "none"


def test_low_quality_evidence_triggers_search_when_threshold_opted_in():
    # C review 后默认阈值改为 0（禁用）；要测低分触发必须显式开启
    policy = WebSearchPolicy(min_evidence_score=0.5)
    d = policy.decide("最新版本是什么？", evidence=evidence(0.001))

    assert d.should_search is True
    assert d.trigger == "low_evidence_quality"
    assert "0.001" in d.reason


def test_good_local_evidence_prevents_search(policy):
    d = policy.decide("最新版本是什么？", evidence=evidence(0.8))
    assert d.should_search is False
    assert d.trigger == "none"


def test_threshold_is_configurable():
    policy = WebSearchPolicy(min_evidence_score=0.9)
    d = policy.decide("最新版本是什么？", evidence=evidence(0.5))
    assert d.should_search is True


# --------------------------------------------------------------------------- #
# 记录质量
# --------------------------------------------------------------------------- #
def test_decision_has_readable_reason(policy):
    d = policy.decide("DeepSeek 最新版本是什么？")
    assert d.reason
    assert not d.reason.startswith("根据规则")


def test_query_is_truncated():
    policy = WebSearchPolicy(max_query_chars=30)
    d = policy.decide("DeepSeek 最新版本是什么？" * 10, need_web_search=True)
    assert len(d.query) == 30


def test_decision_is_json_serializable(policy):
    d = policy.decide("DeepSeek 最新版本是什么？")
    assert '"should_search"' in d.model_dump_json()


def test_invalid_parameters_are_rejected():
    with pytest.raises(ValueError):
        WebSearchPolicy(min_evidence_score=-1)
    with pytest.raises(ValueError):
        WebSearchPolicy(max_query_chars=10)


# --------------------------------------------------------------------------- #
# C review：RRF 真实分数不能被默认阈值误判
# --------------------------------------------------------------------------- #
def test_realistic_rrf_rank1_does_not_trigger_low_quality():
    """HybridRetriever 双路 rank-1 的 RRF 分只有 ~0.0328。

    默认阈值 0.05 会把这种最好结果判成"低质量"，导致带 latest/current 的
    问题只要检索成功就必然联网。C review 发现后把默认阈值改为 0（禁用）。
    """
    rrf_best = 1 / 61 + 1 / 61  # ≈ 0.0328
    policy = WebSearchPolicy()
    d = policy.decide("最新版本是什么？", evidence=evidence(rrf_best))

    assert policy.min_evidence_score == 0.0
    assert d.should_search is False
    assert d.trigger == "none"


def test_rrf_score_still_counts_as_evidence_presence():
    """分数低但非零仍算"有证据"，不该走 no_local_evidence。"""
    policy = WebSearchPolicy()
    d = policy.decide("最新版本是什么？", evidence=evidence(0.001))

    assert d.should_search is False
    assert d.trigger == "none"


def test_score_threshold_is_opt_in_and_still_works():
    policy = WebSearchPolicy(min_evidence_score=0.5)
    d = policy.decide("最新版本是什么？", evidence=evidence(0.0328))

    assert d.should_search is True
    assert d.trigger == "low_evidence_quality"
