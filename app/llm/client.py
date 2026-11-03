"""LLM 统一调用层（A 负责）。

**全系统唯一的模型出口。** 其他模块禁止直接 import openai，也禁止自己读 API key。

为什么必须统一
--------------
1. RQ4 要统计 token / 成本 / 延迟 —— 只有单一出口才能保证不漏记；
2. 模型要能切换（港大 API ↔ DeepSeek），业务代码不能改；
3. 超时与重试策略要一致，否则各模块行为不同，实验不可比。

用法
----
    from app.llm.client import get_llm

    llm = get_llm()                       # 按 .env 的 LLM_PROVIDER 选实现
    resp = llm.generate([{"role": "user", "content": "hi"}])
    print(resp.text, resp.usage.total_tokens)

没配 key 时会自动退回 MockLLM，这样 B/C 在拿到 key 之前也能开发。
"""

from __future__ import annotations

import time
from typing import Any

from app.core.config import settings
from app.core.errors import LLMError
from app.core.interfaces import LLM
from app.core.mocks import MockLLM
from app.core.types import LLMResponse, Usage

__all__ = ["OpenAICompatibleLLM", "get_llm", "reset_llm_cache"]

# 每百万 token 的美元单价。**换模型前请核对官方价目表**，
# 这里的数字只用于实验时的相对成本比较。
_PRICING: dict[str, tuple[float, float]] = {
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
    "gpt-4o-mini": (0.15, 0.60),
}

# 这些错误值得重试；鉴权类错误重试没有意义
_RETRYABLE = ("timeout", "rate limit", "429", "500", "502", "503", "504", "overloaded")


class OpenAICompatibleLLM:
    """基于 OpenAI 兼容协议的实现，港大 ITS API 与 DeepSeek 共用这一套。"""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout: int | None = None,
        max_retries: int | None = None,
    ) -> None:
        if not api_key:
            raise LLMError(
                "缺少 API key：请在 .env 中设置 DEEPSEEK_API_KEY 或 HKU_API_KEY。"
                "未配置时请使用 MockLLM。"
            )
        from openai import OpenAI  # 延迟导入，未配置 key 时不需要它

        self.model = model
        self.timeout = timeout or settings.llm.timeout
        self.max_retries = max_retries if max_retries is not None else settings.llm.max_retries
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=self.timeout)

    # ------------------------------------------------------------------ #
    def generate(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            t0 = time.perf_counter()
            try:
                resp = self._client.chat.completions.create(**kwargs)
                latency_ms = (time.perf_counter() - t0) * 1000
                return self._to_response(resp, latency_ms)
            except Exception as exc:  # noqa: BLE001 - SDK 异常类型太多，统一判断
                last_error = exc
                if attempt >= self.max_retries or not self._is_retryable(exc):
                    break
                # 指数退避：0.5s, 1s, 2s ...
                time.sleep(0.5 * (2**attempt))

        raise LLMError(f"模型调用失败（{self.model}）：{last_error}") from last_error

    # ------------------------------------------------------------------ #
    def _to_response(self, resp: Any, latency_ms: float) -> LLMResponse:
        choice = resp.choices[0]
        text = (choice.message.content or "").strip()
        raw_usage = getattr(resp, "usage", None)
        prompt_tokens = getattr(raw_usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(raw_usage, "completion_tokens", 0) or 0

        in_price, out_price = _PRICING.get(self.model, (0.0, 0.0))
        cost = (prompt_tokens * in_price + completion_tokens * out_price) / 1_000_000

        return LLMResponse(
            text=text,
            model=getattr(resp, "model", self.model),
            finish_reason=getattr(choice, "finish_reason", None),
            usage=Usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                cost_usd=round(cost, 8),
                llm_calls=1,
                latency_ms=round(latency_ms, 2),
            ),
            raw=None,
        )

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        msg = f"{type(exc).__name__}: {exc}".lower()
        return any(k in msg for k in _RETRYABLE)


# --------------------------------------------------------------------------- #
# 工厂
# --------------------------------------------------------------------------- #
_cache: LLM | None = None


def get_llm(provider: str | None = None, *, force_new: bool = False) -> LLM:
    """按 .env 返回模型实例。没配 key 时退回 MockLLM，保证代码永远能跑。"""
    global _cache
    if _cache is not None and not force_new and provider is None:
        return _cache

    api_key, base_url, model = settings.llm.resolve(provider)
    if not api_key:
        llm: LLM = MockLLM()
    else:
        llm = OpenAICompatibleLLM(api_key=api_key, base_url=base_url, model=model)

    if provider is None:
        _cache = llm
    return llm


def reset_llm_cache() -> None:
    """测试用：清掉缓存，让 .env 的改动立即生效。"""
    global _cache
    _cache = None
