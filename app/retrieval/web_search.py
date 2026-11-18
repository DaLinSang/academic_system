"""网页搜索工具（Issue #41，A 负责）。

用 DuckDuckGo HTML 端点，不需要搜索 API key。抓取结果页后用标准库
`HTMLParser` 抽正文，不引第三方解析依赖（本环境没有 bs4/lxml）。

设计约束
--------
* 与 `Retriever` 同风格：`search(query, top_k) -> list[Evidence]`，失败返回空并写 `last_warning`；
* 可注入 `httpx.Client`——沙箱访问不了外网，测试用 `MockTransport` 提供假 HTML；
* 网页证据走 `RetrievalMethod.WEB`，`page_number=0`，定位靠 `url` + `title`；
* 不把 API key、请求头或 cookie 写进 Evidence / trace。

局限
----
DuckDuckGo 的 HTML 结构会变，解析器是"尽力而为"；对 JS 渲染页面无能为力。
这属于研究原型的边界，不能拿来当生产级搜索引擎用。
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import httpx

from app.core.types import Evidence, RetrievalMethod

__all__ = ["WebSearchTool", "WebResult"]

_DDG_ENDPOINT = "https://html.duckduckgo.com/html/"
_MAX_PAGE_CHARS = 4000
_SKIP_TAGS = {"script", "style", "noscript", "svg", "nav", "footer"}


class WebResult:
    """一条搜索结果，转 Evidence 前的中间结构。"""

    def __init__(self, title: str, url: str, snippet: str = "") -> None:
        self.title = title.strip()
        self.url = url.strip()
        self.snippet = snippet.strip()


class _DDGResultParser(HTMLParser):
    """从 DuckDuckGo 结果页抽标题/链接/摘要。"""

    def __init__(self) -> None:
        super().__init__()
        self.results: list[WebResult] = []
        self._capture: str | None = None
        self._title = ""
        self._url = ""
        self._snippet = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        if tag == "a" and "result__a" in classes:
            self._capture = "link"
            self._title = ""
            self._url = attributes.get("href") or ""
        elif "result__snippet" in classes:
            self._capture = "snippet"
            self._snippet = ""

    def handle_data(self, data: str) -> None:
        if self._capture == "link":
            self._title += data
        elif self._capture == "snippet":
            self._snippet += data

    def handle_endtag(self, tag: str) -> None:
        if self._capture == "link" and tag == "a":
            if self._title and self._url:
                self.results.append(WebResult(self._title, self._normalise_url(self._url)))
            self._capture = None
        elif self._capture == "snippet" and tag in ("a", "div"):
            if self.results and self._snippet:
                self.results[-1].snippet = self._snippet
            self._capture = None

    @staticmethod
    def _normalise_url(href: str) -> str:
        # DDG 把目标地址藏在 /l/?uddg=<encoded>
        if "/l/" in href or "uddg=" in href:
            query = parse_qs(urlparse(href).query)
            encoded = query.get("uddg", [""])[0]
            if encoded:
                return unquote(encoded)
        return href


class _TextParser(HTMLParser):
    """从网页正文里抽可见文字，跳过 script/style。"""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = re.sub(r"\s+", " ", data).strip()
        if text:
            self.parts.append(text)

    def text(self, limit: int = _MAX_PAGE_CHARS) -> str:
        return " ".join(self.parts)[:limit]


class WebSearchTool:
    """搜索网页并转成 Evidence。

    参数
    ----
    client: 可注入的 httpx.Client。测试用 MockTransport，生产用默认。
    fetch_pages: 是否抓取结果页正文。关掉时只用搜索摘要，快但证据弱。
    """

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        timeout: float = 10.0,
        fetch_pages: bool = True,
        max_page_chars: int = _MAX_PAGE_CHARS,
    ) -> None:
        self._client = client or httpx.Client(timeout=timeout, follow_redirects=True)
        self._owns_client = client is None
        self.fetch_pages = fetch_pages
        self.max_page_chars = max_page_chars
        self.last_warning: str | None = None

    # ------------------------------------------------------------------ #
    def search(self, query: str, top_k: int = 5) -> list[Evidence]:
        """失败返回空列表并写 last_warning，绝不抛异常打断回答。"""
        self.last_warning = None
        if top_k <= 0 or not query.strip():
            return []

        try:
            response = self._client.get(_DDG_ENDPOINT, params={"q": query})
            response.raise_for_status()
            parser = _DDGResultParser()
            parser.feed(response.text)
            results = parser.results[:top_k]
        except Exception as exc:  # noqa: BLE001 - 网络异常类型太多
            self.last_warning = f"网页搜索不可用：{type(exc).__name__}: {exc}"
            return []

        if not results:
            self.last_warning = "网页搜索未返回结果"
            return []

        return [self._to_evidence(index, item) for index, item in enumerate(results, start=1)]

    # ------------------------------------------------------------------ #
    def _to_evidence(self, index: int, item: WebResult) -> Evidence:
        text = item.snippet
        if self.fetch_pages and item.url:
            page_text = self._fetch_text(item.url)
            if page_text:
                text = page_text
            elif self.last_warning is None:
                self.last_warning = f"网页正文抓取失败，使用搜索摘要：{item.url}"

        domain = urlparse(item.url).netloc or "web"
        return Evidence(
            chunk_id=f"web-{index}",
            document_id=item.url,
            file_name=domain,
            page_number=0,
            text=text or "（无正文）",
            score=max(0.0, 1.0 - index * 0.05),
            retrieval_method=RetrievalMethod.WEB,
            url=item.url,
            title=item.title or domain,
        )

    # ------------------------------------------------------------------ #
    def _fetch_text(self, url: str) -> str:
        try:
            response = self._client.get(url)
            response.raise_for_status()
        except Exception:  # noqa: BLE001 - 单页失败不该毁掉整次搜索
            return ""
        parser = _TextParser()
        parser.feed(response.text)
        return parser.text(self.max_page_chars)

    # ------------------------------------------------------------------ #
    def close(self) -> None:
        if self._owns_client:
            self._client.close()


def escape_query(query: str) -> str:
    """给需要拼 URL 的调用方用，避免注入特殊字符。"""
    return quote_plus(query.strip())
