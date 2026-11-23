"""Tavily 搜索后端（Issue #89）。

与 WebSearchTool 同接口：`search(query, top_k) -> list[Evidence]`、
`last_warning`、`last_diagnostics`。差别：

- 走 Tavily 官方 API（HTTP POST），不是爬 DDG/Bing HTML。
- Tavily 直接返回**已清洗的正文**（content），无需再抓网页——
  这解决了 HTML 方案里"抓正文 403/超时、拿摘要冒充全文"的老问题。
- 结果自带 score（相关性），直接作为 Evidence.score。

没有 API key 时不构造本类，factory 会回退到既有 WebSearchTool。
绝不记录 api_key 或完整查询到日志（Issue #71 的诊断脱敏约定）。
"""

from __future__ import annotations

from urllib.parse import urlparse

import httpx

from app.core.config import settings
from app.core.types import Evidence, RetrievalMethod
from app.retrieval.web_search import _assert_safe_url, _chunk_id

__all__ = ["TavilySearchTool"]


class TavilySearchTool:
    """用 Tavily API 做网页搜索，返回可直接引用的网页证据。"""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float | None = None,
        depth: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.search.tavily_api_key
        self.base_url = (base_url or settings.search.tavily_base_url).rstrip("/")
        self.timeout = timeout if timeout is not None else settings.search.tavily_timeout
        self.depth = depth or settings.search.tavily_depth
        self._client = client or httpx.Client(timeout=self.timeout, follow_redirects=True)
        self._owns_client = client is None
        self.last_warning: str | None = None
        self.last_diagnostics: list[dict[str, object]] = []
        # Issue #96 review：搜索 credits 单列统计，不混进 LLM token cost（RQ4）
        self.last_search_calls = 0
        self.last_search_credits = 0

    def search(self, query: str, top_k: int = 5) -> list[Evidence]:
        """失败返回空列表并写 last_warning，绝不抛异常打断回答。"""
        self.last_warning = None
        self.last_diagnostics = []
        self.last_search_calls = 0
        self.last_search_credits = 0
        if top_k <= 0 or not query.strip():
            return []
        if not self.api_key:
            self.last_warning = "Tavily API key 未配置"
            self.last_diagnostics.append({"backend": "tavily", "kind": "no_api_key"})
            return []

        response: httpx.Response | None = None
        try:
            response = self._client.post(
                f"{self.base_url}/search",
                json={
                    "api_key": self.api_key,
                    "query": query,
                    "search_depth": self.depth,
                    "max_results": top_k,
                },
            )
            response.raise_for_status()
            payload = response.json()
            # 每次成功的 API 搜索消耗 1 credit（basic 深度）
            self.last_search_calls = 1
            self.last_search_credits = 1
        except Exception as exc:  # noqa: BLE001 - 网络/解析异常统一兜底
            self.last_warning = f"Tavily 搜索失败: {type(exc).__name__}"
            self.last_diagnostics.append({
                "backend": "tavily",
                "kind": type(exc).__name__,
                "http_status": response.status_code if response is not None else None,
            })
            return []

        results = payload.get("results") or []
        if not isinstance(results, list):
            self.last_warning = "Tavily 返回结果格式异常"
            self.last_diagnostics.append({"backend": "tavily", "kind": "bad_payload"})
            return []

        evidence: list[Evidence] = []
        for item in results[:top_k]:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            try:
                _assert_safe_url(url)  # SSRF 防护：与 HTML 后端同一套规则
            except Exception:  # noqa: BLE001 - 不安全链接直接跳过，不写进证据
                continue
            content = str(item.get("content") or "").strip()
            title = str(item.get("title") or "").strip()
            domain = urlparse(url).netloc or "web"
            try:
                score = float(item.get("score", 0.0))
            except (TypeError, ValueError):
                score = 0.0
            evidence.append(Evidence(
                chunk_id=_chunk_id(url),
                document_id=url,
                file_name=domain,
                page_number=0,  # 网页证据无页码
                text=content[:4000] or "（无正文）",
                score=score,
                retrieval_method=RetrievalMethod.WEB,
                url=url,
                title=title or domain,
            ))

        if not evidence:
            self.last_warning = "Tavily 未返回可用结果"
        self.last_diagnostics.append({
            "backend": "tavily",
            "kind": "ok" if evidence else "zero_results",
            "count": len(evidence),
            "http_status": response.status_code,
        })
        return evidence

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
