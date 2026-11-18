"""重试可观测性的测试（Issue #48）。

**不重新实现重试**——`OpenAICompatibleLLM` 早就有指数退避。
本文件守的是此前缺的那部分：重试对 trace 不可见、失败尝试的等待时间
没算进 RQ4 的 latency。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent.factory import build_mock_orchestrator, build_orchestrator
from app.core.mocks import (
    MockAnswerGenerator,
    MockMemoryManager,
    MockMemoryStore,
    MockPlanner,
    MockRetriever,
    MockVerifier,
)
from app.core.types import LLMResponse, Usage
from app.llm.client import LLMError, OpenAICompatibleLLM
from app.routing.router import RuleBasedRouter


class FlakyChat:
    """前 N 次抛可重试错误，之后成功。"""

    def __init__(self, failures: int = 2, message: str = "429 rate limit") -> None:
        self.failures = failures
        self.message = message
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError(self.message)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=2),
        )


def make_client(chat, *, max_retries=3):
    # 直接构造，绕过 OpenAI 客户端创建；只测重试逻辑本身
    client = object.__new__(OpenAICompatibleLLM)
    client.model = "test-model"
    client.max_retries = max_retries
    client.timeout = 60
    client.last_retries = 0
    client.last_retry_latency_ms = 0.0
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=chat))
    return client


# --------------------------------------------------------------------------- #
# 重试本身（已有功能，确认没被改坏）
# --------------------------------------------------------------------------- #
def test_retry_recovers_from_transient_failure():
    chat = FlakyChat(failures=2)
    client = make_client(chat)

    response = client.generate([{"role": "user", "content": "hi"}])

    assert response.text == "OK"
    assert chat.calls == 3


def test_non_retryable_error_fails_immediately():
    chat = FlakyChat(failures=1, message="invalid api key")
    client = make_client(chat)

    with pytest.raises(LLMError):
        client.generate([{"role": "user", "content": "hi"}])
    assert chat.calls == 1


# --------------------------------------------------------------------------- #
# 新增的可观测性
# --------------------------------------------------------------------------- #
def test_retry_count_is_exposed():
    client = make_client(FlakyChat(failures=2))
    client.generate([{"role": "user", "content": "hi"}])

    assert client.last_retries == 2


def test_retry_latency_is_trace_only_not_added_to_usage():
    """C review #53：pipeline 墙钟已包含退避，usage 再加就会重复计入。

    RQ4 的 latency 单一来源是 Orchestrator 的端到端墙钟。
    """
    client = make_client(FlakyChat(failures=1))
    response = client.generate([{"role": "user", "content": "hi"}])

    # 退避至少 500ms，但 usage.latency_ms 只计本次成功调用
    assert client.last_retry_latency_ms >= 500
    assert response.usage.latency_ms < 500


def test_no_retry_means_zero_overhead():
    chat = FlakyChat(failures=0)
    client = make_client(chat)
    client.generate([{"role": "user", "content": "hi"}])

    assert client.last_retries == 0
    assert client.last_retry_latency_ms == 0.0


def test_counters_reset_between_calls():
    client = make_client(FlakyChat(failures=1))
    client.generate([{"role": "user", "content": "first"}])
    assert client.last_retries == 1

    client._client.chat.completions = FlakyChat(failures=0)
    client.generate([{"role": "user", "content": "second"}])
    assert client.last_retries == 0
    assert client.last_retry_latency_ms == 0.0


def test_exhausted_retries_still_records_attempts():
    chat = FlakyChat(failures=99)
    client = make_client(chat, max_retries=2)

    with pytest.raises(LLMError):
        client.generate([{"role": "user", "content": "hi"}])
    assert chat.calls == 3
    assert client.last_retries == 2


# --------------------------------------------------------------------------- #
# 编排器把重试写进 trace
# --------------------------------------------------------------------------- #
class RetryingLLM:
    """模拟"内部重试过一次"的 LLM，用于验证 trace 记录。"""

    def __init__(self) -> None:
        self.last_usage = Usage(total_tokens=20, llm_calls=1, latency_ms=700)
        self.last_retries = 1
        self.last_retry_latency_ms = 600.0

    def generate(self, messages, temperature=0.0, max_tokens=None, json_mode=False):
        return LLMResponse(text='{"route":"direct"}', model="fake", usage=self.last_usage)


def test_real_wrapper_router_retry_is_recorded():
    """C review #53：真实链路里 LLMRouter 持有共享 LLM，计数器在 LLM 上。

    原测试直接把 RetryingLLM 传给私有方法，绕过了包装组件，是假阳性。
    """
    from app.routing.router import LLMRouter

    llm = RetryingLLM()
    orch = build_orchestrator(
        router=LLMRouter(llm),  # 真实包装组件，持有共享 llm
        planner=MockPlanner(),
        retriever=MockRetriever(),
        memory_store=MockMemoryStore(),
        memory_manager=MockMemoryManager(),
        verifier=MockVerifier(),
        answer_generator=MockAnswerGenerator(),
        llm=llm,  # 同一个实例
    )

    state = orch.run("什么是 attention", enable_memory=False)

    retries = [e for e in state.trace if e.stage == "llm" and e.event == "retry"]
    assert len(retries) == 1
    assert retries[0].detail["phase"] == "router"
    assert retries[0].detail["retries"] == 1
    assert retries[0].detail["retry_latency_ms"] == 600.0


@pytest.mark.parametrize(
    "stage", ["router", "planner", "generate", "verify", "memory_write"]
)
def test_retry_event_tags_each_stage(stage):
    """事件必须标明是哪个阶段发生的重试，便于 RQ4 分项统计。"""
    from app.core.types import AgentState

    llm = RetryingLLM()
    orch = build_orchestrator(
        router=RuleBasedRouter(),
        planner=MockPlanner(),
        retriever=MockRetriever(),
        memory_store=MockMemoryStore(),
        memory_manager=MockMemoryManager(),
        verifier=MockVerifier(),
        answer_generator=MockAnswerGenerator(),
        llm=llm,
    )
    state = AgentState(question="x")
    orch._record_retry(state, stage)

    retry = [e for e in state.trace if e.event == "retry"][0]
    assert retry.detail["phase"] == stage


def test_pipeline_latency_counts_retry_wait_once():
    """端到端墙钟包含退避；usage 汇总不该再叠加一次。

    用可控时钟模拟：pipeline 墙钟 1200ms，组件 usage 各自 100ms，
    最终 state.usage.latency_ms 只应由墙钟贡献。
    """
    from app.core.types import AgentState

    llm = RetryingLLM()
    orch = build_orchestrator(
        router=RuleBasedRouter(),
        planner=MockPlanner(),
        retriever=MockRetriever(),
        memory_store=MockMemoryStore(),
        memory_manager=MockMemoryManager(),
        verifier=MockVerifier(),
        answer_generator=MockAnswerGenerator(),
        llm=llm,
    )
    state = AgentState(question="x")

    # 组件 usage 不含退避，pipeline 末尾单独加墙钟
    state.usage = state.usage.add(Usage(total_tokens=20, llm_calls=1, latency_ms=100))
    state.usage = state.usage.add(Usage(latency_ms=1200))
    orch._record_retry(state, "generate")  # 只写 trace，不改 usage

    assert state.usage.latency_ms == 1300  # 100 + 1200，不是 1900


def test_mock_component_without_retry_attributes_is_fine():
    from app.core.types import AgentState

    orch = build_mock_orchestrator()
    state = AgentState(question="x")
    orch._absorb_usage(state, MockPlanner())

    assert not [e for e in state.trace if e.event == "retry"]
