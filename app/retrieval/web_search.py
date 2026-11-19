"""网页搜索工具（Issue #41，A 负责）。

不需要搜索 API key：先用 DuckDuckGo HTML 端点，不可达时自动降级到 Bing HTML。
抓取结果页后用标准库 `HTMLParser` 抽正文，不引第三方解析依赖（本环境没有 bs4/lxml）。

设计约束
--------
* 与 `Retriever` 同风格：`search(query, top_k) -> list[Evidence]`，失败返回空并写 `last_warning`；
* 可注入 `httpx.Client`——测试用 `MockTransport` 提供假 HTML；
* 网页证据走 `RetrievalMethod.WEB`，`page_number=0`，定位靠 `url` + `title`；
* 不把 API key、请求头或 cookie 写进 Evidence / trace。

局限
----
两家引擎的 HTML 结构都会变，解析器是"尽力而为"；对 JS 渲染页面无能为力。
部分网络（如中国大陆出口）DuckDuckGo 完全不可达，此时每次搜索先等 5s 超时再走 Bing；
Bing 在某些区域会对纯英文查询做词典式改写，导致学术查询命中词典页，
含中文的查询通常正常。这属于研究原型的边界，不能当生产级搜索引擎用。
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import httpx

from app.core.types import Evidence, RetrievalMethod

__all__ = ["WebSearchTool", "WebResult"]

_DDG_ENDPOINT = "https://html.duckduckgo.com/html/"
_BING_ENDPOINT = "https://www.bing.com/search"
# 后端优先级：DDG 不需要 key、隐私更好；但部分网络（如中国大陆）不可达，
# 超时后自动降级到 Bing。两者都不需要 API key（Issue #41）。
_SEARCH_BACKENDS = (_DDG_ENDPOINT, _BING_ENDPOINT)
_MAX_PAGE_CHARS = 4000
_MAX_PAGE_BYTES = 2_000_000
_ALLOWED_CONTENT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
_LOCAL_HOSTS = {"localhost", "localhost.localdomain", "ip6-localhost"}

# C review #55：chunk_id 用 web-{index} 会在多次 search 后碰撞，
# state.add_evidence() 按 chunk_id 去重会静默丢掉后续查询结果。
def _chunk_id(url: str) -> str:
    digest = hashlib.sha256(url.strip().encode("utf-8")).hexdigest()[:16]
    return f"web-{digest}"


def _assert_safe_url(url: str) -> None:
    """只放行公网 http/https，挡住 localhost / 私网 / 链路本地。"""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"不允许的 URL scheme: {parsed.scheme}")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host or host in _LOCAL_HOSTS or host.endswith((".local", ".internal")):
        raise ValueError(f"不允许访问本地主机名: {host}")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return  # 普通域名，交给 DNS 解析
    if not ip.is_global or ip.is_loopback or ip.is_link_local:
        raise ValueError(f"不允许访问私网/本地地址: {host}")


_SKIP_TAGS = {"script", "style", "noscript", "svg", "nav", "footer"}


class WebResult:
    """一条搜索结果，转 Evidence 前的中间结构。"""

    def __init__(self, title: str, url: str, snippet: str = "") -> None:
        self.title = title.strip()
        self.url = url.strip()
        self.snippet = snippet.strip()


class _SearchResultParser(HTMLParser):
    """从搜索结果页抽标题/链接/摘要，同时支持 DuckDuckGo 与 Bing 的 HTML 结构。

    DDG: ``a.result__a`` 标题链接 + ``a.result__snippet`` 摘要。
    Bing: ``li.b_algo`` 容器内 ``h2 > a`` 标题链接 + ``.b_caption p`` 摘要。
    两种结构互不干扰，按 backend 参数选择解析规则。
    """

    def __init__(self, backend: str = "ddg") -> None:
        super().__init__()
        self.backend = backend
        self.results: list[WebResult] = []
        self._capture: str | None = None
        self._title = ""
        self._url = ""
        self._snippet = ""
        self._in_bing_algo = False
        self._in_bing_h2 = False
        self._in_bing_caption = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        if self.backend == "ddg":
            if tag == "a" and "result__a" in classes:
                self._capture = "link"
                self._title = ""
                self._url = attributes.get("href") or ""
            elif "result__snippet" in classes:
                self._capture = "snippet"
                self._snippet = ""
            return

        # Bing：li.b_algo 标记一条结果，h2 内的第一个 a 是标题链接
        if tag == "li" and "b_algo" in classes:
            self._in_bing_algo = True
            self._url = ""
        if self._in_bing_algo and tag == "h2":
            self._in_bing_h2 = True
        if self._in_bing_h2 and tag == "a" and self._capture is None:
            self._capture = "link"
            self._title = ""
            self._url = attributes.get("href") or ""
        if self._in_bing_algo and tag == "div" and "b_caption" in classes:
            self._in_bing_caption = True
        if self._in_bing_caption and tag == "p" and self._capture is None:
            self._capture = "snippet"
            self._snippet = ""

    def handle_data(self, data: str) -> None:
        if self._capture == "link":
            self._title += data
        elif self._capture == "snippet":
            self._snippet += data

    def handle_endtag(self, tag: str) -> None:
        if self.backend == "ddg":
            if self._capture == "link" and tag == "a":
                if self._title and self._url:
                    self.results.append(WebResult(self._title, self._normalise_url(self._url)))
                self._capture = None
            elif self._capture == "snippet" and tag in ("a", "div"):
                if self.results and self._snippet:
                    self.results[-1].snippet = self._snippet
                self._capture = None
            return

        if self._capture == "link" and tag == "a":
            if self._title and self._url:
                self.results.append(WebResult(self._title, self._url))
            self._capture = None
            self._in_bing_h2 = False
            self._url = ""
        elif self._capture == "snippet" and tag == "p":
            if self.results and self._snippet:
                self.results[-1].snippet = self._snippet
            self._capture = None
            self._in_bing_caption = False
        elif tag == "li":
            self._in_bing_algo = False
            self._in_bing_h2 = False
            self._in_bing_caption = False

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
        # 5s：DDG 不可达时快速失败并切到 Bing；可达时足够返回。
        # 需要通过构造参数覆盖时传 timeout= 即可。
        timeout: float = 5.0,
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

        results: list[WebResult] = []
        failures: list[str] = []
        for endpoint in _SEARCH_BACKENDS:
            backend = "ddg" if "duckduckgo" in endpoint else "bing"
            try:
                _assert_safe_url(endpoint)
                # Bing 指定英文界面，避免中文区域词污染学术查询
                params = {"q": query}
                if backend == "bing":
                    params["setlang"] = "en-US"
                response = self._client.get(endpoint, params=params)
                _assert_safe_url(str(response.url))  # 检查重定向后的最终地址
                response.raise_for_status()
                parser = _SearchResultParser(backend=backend)
                parser.feed(response.text)
                results = parser.results[:top_k]
            except Exception as exc:  # noqa: BLE001 - 网络异常类型太多
                failures.append(f"{backend}: {type(exc).__name__}")
                continue
            if results:
                break
            failures.append(f"{backend}: 未返回结果")

        if not results:
            self.last_warning = "网页搜索不可用：" + "; ".join(failures)
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
            chunk_id=_chunk_id(item.url),
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
            _assert_safe_url(url)
            response = self._client.get(url)
            _assert_safe_url(str(response.url))  # 重定向后重新检查，防 SSRF 绕过
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
            if content_type and content_type not in _ALLOWED_CONTENT_TYPES:
                self.last_warning = f"网页内容类型不受支持: {content_type}"
                return ""
            body = response.content[:_MAX_PAGE_BYTES]
            if len(response.content) > _MAX_PAGE_BYTES:
                self.last_warning = "网页响应过大，只处理前 2MB"
        except Exception as exc:  # noqa: BLE001 - 单页失败不该毁掉整次搜索
            self.last_warning = f"网页抓取被拒绝: {type(exc).__name__}: {exc}"
            return ""
        parser = _TextParser()
        parser.feed(body.decode(response.encoding or "utf-8", errors="replace"))
        return parser.text(self.max_page_chars)

    # ------------------------------------------------------------------ #
    def close(self) -> None:
        if self._owns_client:
            self._client.close()


def escape_query(query: str) -> str:
    """给需要拼 URL 的调用方用，避免注入特殊字符。"""
    return quote_plus(query.strip())
