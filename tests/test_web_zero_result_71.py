"""Issue #71：网页搜索零结果后仍生成未核实回答的回归测试。

根因：WebSearchTool 返回 [] + last_warning 后，Orchestrator 只记 warning，
     AnswerGenerator 遇到 route=direct 仍调用 LLM，模型凭记忆编出"根据官网..."
     和假 URL，UI 最后才补"未找到结果"——承诺与事实相反。

本文件守：
1. 明确决定联网但 web evidence=0 → 不调 LLM，返回明确失败说明（独立于 route）
2. 未决定联网 / 有真实网页证据 → 正常生成（不误伤）
3. 诊断信息结构化记录后端、失败类别、HTTP 状态，且不含查询词/响应正文
4. 成功时只引用真实 Evidence.url
"""

from __future__ import annotations

import httpx

from app.agent.answer_generator import LLMAnswerGenerator
from app.core.types import (
    AgentState,
    Evidence,
    LLMResponse,
    RetrievalMethod,
    RouteDecision,
    RouteType,
    TraceEvent,
    Usage,
)
from app.retrieval.web_search import WebSearchTool


class CountingLLM:
    """记录调用次数；若不该调用却被调用，测试会失败。"""

    def __init__(self, text: str = "根据官网，COMP7608 是高级机器学习。") -> None:
        self.text = text
        self.calls = 0
        self.prompts: list[str] = []

    def generate(self, messages, **kwargs):
        self.calls += 1
        self.prompts.append(messages[-1]["content"])
        return LLMResponse(text=self.text, usage=Usage(total_tokens=10), model="fake")


def web_state(*, decided: bool, count: int, warning=None, has_web_evidence=False):
    s = AgentState(question="请查询香港大学官网对 COMP7608 的最新介绍，给出实际链接。")
    s.route = RouteDecision(route=RouteType.DIRECT, need_retrieval=False, reason="direct")
    s.trace.append(TraceEvent(
        stage="web_search", event="decision",
        detail={"should_search": decided, "trigger": "router_request" if decided else "none"},
    ))
    if decided:
        s.trace.append(TraceEvent(
            stage="web_search", event="end",
            detail={"count": count, "warning": warning, "diagnostics": []},
        ))
    if has_web_evidence:
        s.evidence = [Evidence(
            chunk_id="web-1", document_id="https://www.hku.hk/comp7608",
            file_name="hku.hk", page_number=0, text="COMP7608 Large language models",
            score=0.9, retrieval_method=RetrievalMethod.WEB,
            url="https://www.hku.hk/comp7608", title="HKU COMP7608",
        )]
    return s


# --------------------------------------------------------------------------- #
# 1. 零结果阻断
# --------------------------------------------------------------------------- #
def test_web_failed_does_not_call_llm():
    """明确要求联网但零结果：不调 LLM，不编造官网介绍/URL。"""
    llm = CountingLLM()
    gen = LLMAnswerGenerator(llm)
    state = web_state(decided=True, count=0, warning="ddg: zero_results; bing: ConnectError")

    answer = gen.generate(state)

    assert llm.calls == 0, "联网失败时不能调用 LLM 编造事实"
    assert "根据官网" not in answer
    assert "高级机器学习" not in answer  # 模型可能编的错误内容
    assert "没有取得可用的网页结果" in answer
    assert "ConnectError" in answer
    assert any(e.event == "web_search_failed" for e in state.trace)


def test_web_failed_even_when_route_direct():
    """阻断独立于 route=direct（Issue #71 明确要求）。"""
    llm = CountingLLM()
    gen = LLMAnswerGenerator(llm)
    state = web_state(decided=True, count=0)
    assert state.route.is_direct

    gen.generate(state)
    assert llm.calls == 0


def test_no_web_decision_uses_normal_direct():
    """未决定联网时，direct 正常走模型（不误伤普通问答）。"""
    llm = CountingLLM("这是正常回答。")
    gen = LLMAnswerGenerator(llm)
    state = web_state(decided=False, count=0)

    answer = gen.generate(state)
    assert llm.calls == 1
    assert answer == "这是正常回答。"


def test_web_success_with_evidence_uses_grounded_path():
    """取得真实网页证据时，正常走有证据生成路径。"""
    llm = CountingLLM("根据证据 [1]，COMP7608 是 Large Language Models 课程 [1]。")
    gen = LLMAnswerGenerator(llm)
    state = web_state(decided=True, count=1, has_web_evidence=True)

    answer = gen.generate(state)
    assert llm.calls == 1
    assert "https://www.hku.hk/comp7608" in llm.prompts[0]
    assert "[1]" in answer


# --------------------------------------------------------------------------- #
# 2. 结构化诊断（不含敏感内容）
# --------------------------------------------------------------------------- #
def test_diagnostics_record_backend_kind_and_status():
    """两个后端失败：诊断记录类别与 HTTP 状态，不记查询词。"""
    secret_query = "我的 API key 是 sk-secret123456 请搜索"

    def handler(request):
        if "duckduckgo" in str(request.url):
            return httpx.Response(503, text="<html>blocked</html>")
        raise httpx.ConnectError("connection failed")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    tool = WebSearchTool(client=client, timeout=2, fetch_pages=False)
    results = tool.search(secret_query, top_k=3)

    assert results == []
    kinds = {d["backend"]: d["kind"] for d in tool.last_diagnostics}
    assert kinds["ddg"] == "HTTPStatusError"
    assert kinds["bing"] == "ConnectError"
    assert any(d.get("http_status") == 503 for d in tool.last_diagnostics)
    # 诊断绝不泄漏查询词
    assert "sk-secret123456" not in str(tool.last_diagnostics)
    assert "sk-secret123456" not in str(tool.last_warning)


def test_zero_results_diagnostic():
    """HTTP 200 但解析 0 条：诊断记 zero_results + 状态码。"""
    def handler(request):
        return httpx.Response(200, text="<html></html>")

    tool = WebSearchTool(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        timeout=2, fetch_pages=False,
    )
    assert tool.search("test", top_k=3) == []
    assert all(d["kind"] == "zero_results" for d in tool.last_diagnostics)
    assert all(d["http_status"] == 200 for d in tool.last_diagnostics)
