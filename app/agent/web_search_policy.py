"""Web 搜索触发决策（Issue #40，A 负责）。

**本模块只做决策，不发任何网络请求。** 执行器由 B 的 `WebSearchTool` 提供，
编排器在工具就绪后调用本模块再决定是否执行。拆开的好处：

1. 决策逻辑可以离线测试，不需要真的联网；
2. 契约 Issue #39 还没合并时，这一部分就能先做完；
3. RQ1 要单独统计"该联网却没联网"和"不该联网却联网"，
   必须有一个独立的、可审查的判断点。

为什么需要"本地证据不足才联网"
------------------------------
无脑联网有三个坏处：成本、延迟、以及把用户的私有问题发到外网。
所以默认不联网，只有满足下面之一才触发：

- Router / Planner 明确说需要（`need_web_search=True`）
- 本地检索没找到证据，且问题涉及时效性或外部事实

隐私红线：含手机/邮箱/密钥/身份证的问题一律不联网。
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field

from app.core.types import Evidence, _Base
from app.memory.manager import find_sensitive

__all__ = ["WebSearchDecision", "WebSearchPolicy", "WebSearchTrigger"]


WebSearchTrigger = Literal[
    "none",
    "router_request",
    "no_local_evidence",
    "low_evidence_quality",
    "sensitive_blocked",
    "mock_blocked",
    "disabled",
]


class WebSearchDecision(_Base):
    """一次联网决策的完整记录，会写进 trace 供评测读取。"""

    should_search: bool
    trigger: WebSearchTrigger = "none"
    reason: str = Field(default="", description="一句中文，写进报告")
    query: str = Field(default="", description="实际要搜的查询串，不联网时为空")


class WebSearchPolicy:
    """决定本轮该不该联网。

    参数
    ----
    enabled: 总开关。False 时永远不联网（B1/B2 或实验对照用）。
    allow_mock: Mock 模式是否允许联网。默认 False——离线演示不该产生外网流量。
    min_evidence_score: 低于这个分数视为"证据质量不足"。**0 表示禁用分数阈值**（默认）。
        RRF 融合分数量级只有 0.03 左右，在 C 用标注集校准之前不能拿绝对阈值判断。
        只有"有没有证据"是可靠信号。
    """

    # 时效性 / 外部事实信号。命中才认为"本地讲义可能答不了"。
    _EXTERNAL_SIGNALS = (
        # 中文
        "最新", "今天", "今日", "现在", "目前", "近期", "最近", "新闻",
        "官网", "网上", "网页", "发布", "更新", "版本", "价格", "股价",
        "天气", "2024", "2025", "2026",
        # 英文
        "latest", "today", "current", "now", "recently", "recent", "news",
        "official", "website", "release", "version", "price", "stock",
        "2024", "2025", "2026",
    )
    _QUESTION_MARK = re.compile(r"[?？]$")
    # 联网专属红线：这些内容绝不能发到外网，即使记忆模块的正则没拦住
    _WEB_SENSITIVE_KEYWORDS = (
        "密钥", "私钥", "密码", "口令", "凭据", "token", "secret",
        "credential", "authorization", "bearer ",
    )

    def __init__(
        self,
        *,
        enabled: bool = True,
        allow_mock: bool = False,
        min_evidence_score: float = 0.0,
        max_query_chars: int = 200,
    ) -> None:
        if min_evidence_score < 0:
            raise ValueError("min_evidence_score 不能为负")
        if max_query_chars < 20:
            raise ValueError("max_query_chars 至少 20")
        self.enabled = enabled
        self.allow_mock = allow_mock
        self.min_evidence_score = min_evidence_score
        self.max_query_chars = max_query_chars

    # ------------------------------------------------------------------ #
    def decide(
        self,
        question: str,
        *,
        need_web_search: bool = False,
        evidence: list[Evidence] | None = None,
        retrieval_attempted: bool = False,
        mock_mode: bool = False,
    ) -> WebSearchDecision:
        """按优先级判断。先挡风险和开关，再看显式要求，最后看证据。"""
        text = question.strip()
        if not text:
            return WebSearchDecision(should_search=False, trigger="none", reason="空问题不联网")

        if not self.enabled:
            return WebSearchDecision(should_search=False, trigger="disabled", reason="Web 搜索总开关关闭")

        if mock_mode and not self.allow_mock:
            return WebSearchDecision(should_search=False, trigger="mock_blocked", reason="Mock 模式不产生外网流量")

        # 隐私红线：绝不把含联系方式/密钥的问题发出去
        lowered = text.casefold()
        sensitive = find_sensitive(text) or next(
            (word for word in self._WEB_SENSITIVE_KEYWORDS if word in lowered), None
        )
        if sensitive is not None:
            return WebSearchDecision(
                should_search=False, trigger="sensitive_blocked",
                reason=f"问题含敏感信息（{sensitive}），禁止联网",
            )

        query = text[: self.max_query_chars]

        # 显式要求：Router / Planner 说了要联网，即使本地有证据也照办
        if need_web_search:
            return WebSearchDecision(
                should_search=True, trigger="router_request",
                reason="路由或规划明确要求联网", query=query,
            )

        items = evidence or []
        if not self._looks_external(text):
            return WebSearchDecision(
                should_search=False, trigger="none",
                reason="问题不涉及时效性或外部事实，本地证据足够或无需联网",
            )

        # 外部问题 + 没证据 → 联网
        if not items:
            reason = (
                "本地检索未找到证据且问题依赖外部事实"
                if retrieval_attempted
                else "未检索到本地证据且问题依赖外部事实"
            )
            return WebSearchDecision(should_search=True, trigger="no_local_evidence", reason=reason, query=query)

        # 外部问题 + 证据分过低 → 联网
        best = max((e.score for e in items), default=0.0)
        # 分数阈值默认关闭：RRF 分数量级 ~0.03，1/61 + 1/61 ≈ 0.0328 已经是
        # 双路 rank-1 的最好结果。用绝对阈值会把正常检索全判成低质量。
        if self.min_evidence_score > 0 and best < self.min_evidence_score:
            return WebSearchDecision(
                should_search=True, trigger="low_evidence_quality",
                reason=f"本地证据最高分 {best:.3f} 低于阈值 {self.min_evidence_score:.3f}",
                query=query,
            )

        return WebSearchDecision(should_search=False, trigger="none", reason="本地已有足够证据，不联网")

    # ------------------------------------------------------------------ #
    def _looks_external(self, text: str) -> bool:
        lowered = text.casefold()
        if any(signal in lowered for signal in self._EXTERNAL_SIGNALS):
            return True
        # "……吗？"且完全没命中外部词时不算；避免所有疑问句都联网
        return False
