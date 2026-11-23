"""联网搜索后端降级包装（Issue #96 review）。

背景：factory 原来按"有没有 Tavily key"二选一。B review 指出：
key 配了但 Tavily 请求失败/返回空时，不会回退到 WebSearchTool，
导致"配了 key 反而不如不配"——这是真 bug。

本包装器实现：先试主后端（Tavily），无有效结果或异常就自动试备用后端（HTML 爬取）。
对外暴露与 WebSearchTool 相同的接口（search / last_warning / last_diagnostics），
Orchestrator 无需改动调用方式。

搜索 credits 单列统计（不混进 LLM token cost，RQ4 口径）：
- last_search_backend: 实际生效的后端名
- last_search_credits: 本次消耗的 API credits
"""

from __future__ import annotations

from app.core.types import Evidence

__all__ = ["FallbackSearchTool"]


class FallbackSearchTool:
    """主后端失败/空结果时自动降级到备用后端。"""

    def __init__(self, primary: object, fallback: object) -> None:
        self.primary = primary
        self.fallback = fallback
        self.last_backend = ""
        self.last_warning: str | None = None
        self.last_diagnostics: list[dict[str, object]] = []
        self.last_search_credits = 0

    def search(self, query: str, top_k: int = 5) -> list[Evidence]:
        """先试主后端；无有效结果就试备用。绝不抛异常打断回答。"""
        self.last_warning = None
        self.last_diagnostics = []
        self.last_search_credits = 0
        backend_name = type(self.primary).__name__

        try:
            found = self.primary.search(query, top_k=top_k)
        except Exception as exc:  # noqa: BLE001 - 主后端异常也要降级
            found = []
            self.last_diagnostics.append({
                "backend": backend_name, "kind": f"primary_error:{type(exc).__name__}",
            })

        if found:
            self.last_backend = backend_name
            self.last_search_credits = int(
                getattr(self.primary, "last_search_credits", 0) or 0
            )
            self.last_warning = getattr(self.primary, "last_warning", None)
            self.last_diagnostics = list(
                getattr(self.primary, "last_diagnostics", []) or []
            )
            self.last_diagnostics.append({"backend": backend_name, "kind": "selected"})
            return found

        # 主后端空结果：记下原因，再试备用
        primary_warning = getattr(self.primary, "last_warning", None)
        primary_diags = list(getattr(self.primary, "last_diagnostics", []) or [])
        self.last_diagnostics.extend(primary_diags)
        self.last_diagnostics.append({
            "backend": backend_name, "kind": "primary_empty_fallback",
        })

        try:
            found = self.fallback.search(query, top_k=top_k)
        except Exception as exc:  # noqa: BLE001 - 两个后端都失败也不能炸
            self.last_backend = "none"
            self.last_warning = (
                f"所有搜索后端均失败（主：{primary_warning or '无结果'}；"
                f"备：{type(exc).__name__}）"
            )
            self.last_diagnostics.append({
                "backend": "fallback", "kind": f"error:{type(exc).__name__}",
            })
            return []

        self.last_warning = getattr(self.fallback, "last_warning", None) or primary_warning
        self.last_diagnostics.extend(
            list(getattr(self.fallback, "last_diagnostics", []) or [])
        )
        if found:
            self.last_backend = type(self.fallback).__name__
            self.last_diagnostics.append({
                "backend": self.last_backend, "kind": "selected_fallback"
            })
        else:
            # 两个后端都没有有效结果
            self.last_backend = "none"
            self.last_diagnostics.append({
                "backend": "fallback", "kind": "empty"
            })
        return found
