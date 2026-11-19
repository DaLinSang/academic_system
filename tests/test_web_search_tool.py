"""WebSearchTool 的测试（Issue #41）。

沙箱访问不了外网，所以全部用 httpx.MockTransport 提供假 HTML。
重点守：转出来的 Evidence 契约字段正确、失败不抛异常、正文里不夹带 script。
"""

from __future__ import annotations

import httpx
import pytest

from app.core.types import RetrievalMethod
from app.retrieval.web_search import WebSearchTool

DDG_HTML = """
<html><body>
<div class="result results_links">
  <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Frag">What is RAG?</a>
  <a class="result__snippet">RAG combines retrieval and generation.</a>
</div>
<div class="result results_links">
  <a rel="nofollow" class="result__a" href="https://docs.test/rag-eval">RAG evaluation</a>
  <a class="result__snippet">How to evaluate a RAG system.</a>
</div>
</body></html>
"""

PAGE_HTML = """
<html><head><script>api_key='sk-should-not-appear'</script></head>
<body><h1>What is RAG?</h1><p>RAG grounds answers in retrieved documents.</p></body></html>
"""


def make_tool(handler, *, fetch_pages=True):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return WebSearchTool(client=client, fetch_pages=fetch_pages)


def two_endpoints(**overrides):
    def handler(request):
        if "duckduckgo" in str(request.url):
            return httpx.Response(200, text=overrides.get("ddg", DDG_HTML))
        return httpx.Response(200, text=overrides.get("page", PAGE_HTML))

    return handler


# --------------------------------------------------------------------------- #
# 正常路径
# --------------------------------------------------------------------------- #
def test_search_returns_web_evidence():
    tool = make_tool(two_endpoints())
    results = tool.search("what is RAG", top_k=2)

    assert len(results) == 2
    assert all(e.is_web for e in results)
    assert all(e.retrieval_method is RetrievalMethod.WEB for e in results)
    assert all(e.page_number == 0 for e in results)


def test_web_evidence_has_url_title_and_locator():
    tool = make_tool(two_endpoints())
    first = tool.search("what is RAG")[0]

    assert first.url == "https://example.com/rag"
    assert first.title == "What is RAG?"
    assert first.locator == "What is RAG? · 网页"
    assert first.file_name == "example.com"


def test_ddg_redirect_url_is_decoded():
    tool = make_tool(two_endpoints())
    results = tool.search("q")

    assert results[0].url.startswith("https://example.com/")
    assert "uddg" not in results[0].url


def test_top_k_is_respected():
    tool = make_tool(two_endpoints())
    assert len(tool.search("q", top_k=1)) == 1


def test_page_body_is_used_when_fetch_enabled():
    tool = make_tool(two_endpoints())
    first = tool.search("q")[0]

    assert "grounds answers" in first.text
    assert "RAG combines retrieval" not in first.text


def test_snippet_used_when_fetch_disabled():
    tool = make_tool(two_endpoints(), fetch_pages=False)
    first = tool.search("q")[0]

    assert "combines retrieval" in first.text


def test_script_content_is_not_indexed():
    tool = make_tool(two_endpoints())
    first = tool.search("q")[0]

    assert "sk-should-not-appear" not in first.text
    assert "api_key" not in first.text


# --------------------------------------------------------------------------- #
# 失败降级
# --------------------------------------------------------------------------- #
def test_network_failure_returns_empty_with_warning():
    def broken(request):
        raise httpx.ConnectError("offline")

    tool = make_tool(broken)
    assert tool.search("q") == []
    assert tool.last_warning and "ConnectError" in tool.last_warning


def test_http_error_returns_empty_with_warning():
    def forbidden(request):
        return httpx.Response(403, text="forbidden")

    tool = make_tool(forbidden)
    assert tool.search("q") == []
    assert tool.last_warning is not None


def test_empty_results_set_warning():
    tool = make_tool(two_endpoints(ddg="<html><body>nothing</body></html>"))
    assert tool.search("q") == []
    assert "未返回结果" in (tool.last_warning or "")


def test_page_failure_falls_back_to_snippet():
    def handler(request):
        if "duckduckgo" in str(request.url):
            return httpx.Response(200, text=DDG_HTML)
        raise httpx.TimeoutException("page timeout")

    tool = make_tool(handler)
    first = tool.search("q")[0]

    assert "combines retrieval" in first.text
    assert (tool.last_warning or "")


def test_warning_is_reset_each_search():
    tool = make_tool(two_endpoints(fetch_pages=False))
    tool.search("q")
    assert tool.last_warning is None


@pytest.mark.parametrize("query", ["", "   "])
def test_empty_query_does_no_network_call(query):
    def explode(request):
        raise AssertionError("不该发请求")

    tool = make_tool(explode)
    assert tool.search(query) == []


def test_zero_top_k_does_no_network_call():
    def explode(request):
        raise AssertionError("不该发请求")

    tool = make_tool(explode)
    assert tool.search("q", top_k=0) == []


# --------------------------------------------------------------------------- #
# 与契约一致
# --------------------------------------------------------------------------- #
def test_evidence_json_contains_web_fields():
    tool = make_tool(two_endpoints(), fetch_pages=False)
    data = tool.search("q")[0].model_dump_json()

    assert '"retrieval_method":"web"' in data
    assert '"url":"https://example.com/rag"' in data
    assert '"page_number":0' in data


# --------------------------------------------------------------------------- #
# C review #55：chunk_id 唯一性与 SSRF 防护
# --------------------------------------------------------------------------- #
def test_chunk_ids_do_not_collide_across_searches():
    """两次不同 URL 的搜索合并到 AgentState 时不能被去重丢掉。"""
    from app.core.types import AgentState

    ddg_one = """
    <div class="result"><a class="result__a" href="https://site.test/one">One</a>
    <a class="result__snippet">first</a></div>
    """
    ddg_two = """
    <div class="result"><a class="result__a" href="https://site.test/two">Two</a>
    <a class="result__snippet">second</a></div>
    """
    state = {"html": ddg_one}

    def handler(request):
        if "duckduckgo" in str(request.url):
            return httpx.Response(200, text=state["html"])
        return httpx.Response(200, text="<html><body>page</body></html>")

    tool = make_tool(handler, fetch_pages=False)
    first = tool.search("one")
    state["html"] = ddg_two
    second = tool.search("two")

    assert first[0].chunk_id != second[0].chunk_id
    agent = AgentState(question="q")
    agent.add_evidence(first)
    agent.add_evidence(second)
    assert len(agent.evidence) == 2


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:5434/admin",
        "http://127.0.0.1:8080/",
        "http://10.0.0.5/latest/meta-data",
        "http://169.254.169.254/latest/meta-data",
        "file:///etc/passwd",
        "ftp://example.com/file",
    ],
)
def test_unsafe_page_url_is_rejected_without_request(url):
    from app.retrieval.web_search import _assert_safe_url

    with pytest.raises(ValueError):
        _assert_safe_url(url)


def test_localhost_page_fetch_does_not_issue_request():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, text="<html></html>")

    tool = make_tool(handler)
    # 抓取失败被降级为空字符串，但绝不能真的发请求
    assert tool._fetch_text("http://localhost:5434/secret") == ""
    assert "拒绝" in (tool.last_warning or "")
    assert calls == [], "绝不能对 localhost 发请求"


def test_public_url_is_allowed():
    from app.retrieval.web_search import _assert_safe_url

    _assert_safe_url("https://example.com/rag")  # 不抛异常即可
