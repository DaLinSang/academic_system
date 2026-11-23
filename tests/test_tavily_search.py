"""Tavily 搜索后端测试（Issue #89）。

守：
1. 正常返回：Tavily results 转成网页 Evidence（含真实 url / 清洗正文 / score）
2. SSRF 防护：不安全 url 被跳过
3. 无 key / HTTP 错误 / 空结果：返回空列表 + 诊断，不抛异常
4. 诊断不泄漏查询词与 key
"""

from __future__ import annotations

import httpx
import pytest

from app.core.types import RetrievalMethod
from app.retrieval.tavily_search import TavilySearchTool


def tool_with(handler, *, key="test-key"):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return TavilySearchTool(key, client=client)


def test_results_become_web_evidence():
    def handler(request):
        assert request.url.path == "/search"
        return httpx.Response(200, json={"results": [
            {"title": "计算机专业", "url": "https://example.edu/cs",
             "content": "课程包括数据结构与算法", "score": 0.89},
        ]})

    tool = tool_with(handler)
    ev = tool.search("计算机科学专业怎么样", top_k=3)

    assert len(ev) == 1
    assert ev[0].url == "https://example.edu/cs"
    assert "数据结构" in ev[0].text
    assert ev[0].score == pytest.approx(0.89)
    assert ev[0].retrieval_method == RetrievalMethod.WEB
    assert ev[0].page_number == 0
    assert tool.last_diagnostics[0]["count"] == 1


def test_no_key_returns_empty():
    def handler(request):
        raise AssertionError("无 key 时不应发请求")

    tool = tool_with(handler, key="")
    assert tool.search("test", top_k=3) == []
    assert tool.last_diagnostics[0]["kind"] == "no_api_key"


def test_http_error_returns_empty_with_diagnostic():
    def handler(request):
        return httpx.Response(401, json={"error": "invalid key"})

    tool = tool_with(handler)
    assert tool.search("test", top_k=3) == []
    assert tool.last_warning is not None
    assert tool.last_diagnostics[0]["http_status"] == 401


def test_empty_results():
    def handler(request):
        return httpx.Response(200, json={"results": []})

    tool = tool_with(handler)
    assert tool.search("test", top_k=3) == []
    assert tool.last_diagnostics[0]["kind"] == "zero_results"


def test_unsafe_url_skipped():
    def handler(request):
        return httpx.Response(200, json={"results": [
            {"title": "local", "url": "http://localhost/secret", "content": "x", "score": 1.0},
            {"title": "ok", "url": "https://example.com/ok", "content": "y", "score": 0.8},
        ]})

    tool = tool_with(handler)
    ev = tool.search("test", top_k=5)
    assert len(ev) == 1
    assert ev[0].url == "https://example.com/ok"


def test_diagnostics_do_not_leak_query_or_key():
    def handler(request):
        return httpx.Response(200, json={"results": []})

    secret_query = "我的 key 是 test-key-12345"
    tool = tool_with(handler)
    tool.search(secret_query, top_k=3)
    blob = str(tool.last_diagnostics) + str(tool.last_warning)
    assert "test-key-12345" not in blob


# --------------------------------------------------------------------------- #
# Issue #96 review：失败回退 + credits 单列统计
# --------------------------------------------------------------------------- #
def test_tavily_credits_counted_per_call():
    def handler(request):
        return httpx.Response(200, json={"results": [
            {"title": "x", "url": "https://example.com/x", "content": "y", "score": 0.5},
        ]})

    tool = tool_with(handler)
    tool.search("test", top_k=3)
    assert tool.last_search_calls == 1
    assert tool.last_search_credits == 1


def test_fallback_tool_uses_primary_when_ok():
    from app.agent.web_search_fallback import FallbackSearchTool
    from app.core.types import Evidence, RetrievalMethod

    class Primary:
        last_warning = None
        last_diagnostics = []
        last_search_credits = 1
        def search(self, q, top_k=5):
            return [Evidence(chunk_id="p", document_id="https://p.com", file_name="p.com",
                             page_number=0, text="good", score=0.9,
                             retrieval_method=RetrievalMethod.WEB, url="https://p.com")]

    class Fallback:
        def search(self, q, top_k=5):
            raise AssertionError("主后端成功时不该调备用")

    tool = FallbackSearchTool(Primary(), Fallback())
    ev = tool.search("test", top_k=3)
    assert len(ev) == 1
    assert "Primary" in tool.last_backend
    assert tool.last_search_credits == 1


def test_fallback_tool_falls_back_when_primary_empty():
    from app.agent.web_search_fallback import FallbackSearchTool
    from app.core.types import Evidence, RetrievalMethod

    class Primary:
        last_warning = "Tavily 未返回可用结果"
        last_diagnostics = [{"backend": "tavily", "kind": "zero_results"}]
        last_search_credits = 1
        def search(self, q, top_k=5):
            return []

    class Fallback:
        last_warning = None
        last_diagnostics = []
        def search(self, q, top_k=5):
            return [Evidence(chunk_id="f", document_id="https://f.com", file_name="f.com",
                             page_number=0, text="fallback result", score=0.7,
                             retrieval_method=RetrievalMethod.WEB, url="https://f.com")]

    tool = FallbackSearchTool(Primary(), Fallback())
    ev = tool.search("test", top_k=3)
    assert len(ev) == 1 and "fallback result" in ev[0].text
    assert "Fallback" in tool.last_backend
    assert any(d.get("kind") == "primary_empty_fallback" for d in tool.last_diagnostics)


def test_fallback_tool_all_fail_returns_empty():
    from app.agent.web_search_fallback import FallbackSearchTool

    class Broken:
        last_warning = "fail"
        last_diagnostics = []
        def search(self, q, top_k=5):
            return []

    tool = FallbackSearchTool(Broken(), Broken())
    assert tool.search("test", top_k=3) == []
    assert tool.last_backend == "none"
    assert tool.last_search_credits == 0
