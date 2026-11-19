"""多轮上下文端到端集成测试（Issue #44）。

覆盖三条注入路径 + 增强验收：
  1. Orchestrator.run(history=...) → state.history 正确落位、turn_index 保留
  2. LLMRouter 把历史格式化进路由 prompt
  3. LLMPlanner 把历史格式化进规划 prompt
  4. AnswerGenerator 把历史格式化进 direct / grounded 两条生成 prompt
  5. 规则路由/Mock 忽略历史，不被关键词污染
  6. 历史为空时 prompt 逐字不变（RQ1 基线可比）

所有测试用假 LLM 捕获实际发出的 prompt，走真实组件路径，
避免"直接调私有方法"的假阳性（C review #53 的教训）。
"""

from __future__ import annotations

from app.agent.answer_generator import LLMAnswerGenerator
from app.agent.factory import build_orchestrator
from app.agent.orchestrator import MODE_PRESETS
from app.core.interfaces import LLM
from app.core.mocks import (
    MockAnswerGenerator,
    MockLLM,
    MockMemoryManager,
    MockMemoryStore,
    MockPlanner,
    MockRetriever,
    MockVerifier,
)
from app.core.types import (
    AgentState,
    ConversationTurn,
    Evidence,
    LLMResponse,
    RetrievalMethod,
    RouteDecision,
    RouteType,
    Usage,
)
from app.planning.planner import LLMPlanner
from app.routing.router import LLMRouter, RuleBasedRouter


class CapturingLLM(LLM):
    """记录每次 generate 的 user prompt，返回可控响应。"""

    def __init__(self, response: str = "{}", usage: Usage | None = None) -> None:
        self.prompts: list[str] = []
        self.response = response
        self.usage = usage or Usage()

    def generate(self, messages, **kwargs):  # noqa: ANN001, ANN003
        self.prompts.append(messages[-1]["content"])
        return LLMResponse(text=self.response, usage=self.usage, model="capture")


def turn(index: int, question: str, answer: str) -> ConversationTurn:
    return ConversationTurn(question=question, answer=answer, turn_index=index)


HISTORY = [
    turn(0, "我在做 RAG 综述", "好的，RAG 是检索增强生成。"),
    turn(1, "它有哪些代表工作？", "代表工作包括 DPR 和 FiD。"),
]


# --------------------------------------------------------------------------- #
# 1. run(history=...) 落位
# --------------------------------------------------------------------------- #
def test_run_places_history_in_state_and_keeps_turn_index():
    orch = build_orchestrator(
        router=RuleBasedRouter(),
        planner=MockPlanner(),
        retriever=MockRetriever(),
        memory_store=MockMemoryStore(),
        memory_manager=MockMemoryManager(),
        verifier=MockVerifier(),
        answer_generator=MockAnswerGenerator(),
        llm=MockLLM(),
    )
    state = orch.run(
        "它主要解决什么问题？",
        history=HISTORY,
        turn_index=2,
        **MODE_PRESETS["B4"],
    )

    assert [t.question for t in state.history] == [h.question for h in HISTORY]
    assert state.turn_index == 2
    assert state.question == "它主要解决什么问题？"


def test_run_without_history_is_unchanged():
    orch = build_orchestrator(
        router=RuleBasedRouter(),
        planner=MockPlanner(),
        retriever=MockRetriever(),
        memory_store=MockMemoryStore(),
        memory_manager=MockMemoryManager(),
        verifier=MockVerifier(),
        answer_generator=MockAnswerGenerator(),
        llm=MockLLM(),
    )
    state = orch.run("我在做 RAG 综述", **MODE_PRESETS["B4"])

    assert state.history == []


# --------------------------------------------------------------------------- #
# 2. LLMRouter prompt 注入
# --------------------------------------------------------------------------- #
def test_llm_router_injects_history_into_prompt():
    llm = CapturingLLM('{"route":"direct","confidence":0.9,"reason":"ok"}')
    router = LLMRouter(llm)

    router.route("它主要解决什么问题？", history=HISTORY)

    prompt = llm.prompts[-1]
    assert "RAG 综述" in prompt
    assert "DPR 和 FiD" in prompt
    assert "它主要解决什么问题？" in prompt
    assert prompt.index("RAG 综述") < prompt.index("它主要解决什么问题？")


def test_llm_router_prompt_identical_when_no_history():
    with_history_llm = CapturingLLM('{"route":"direct"}')
    no_history_llm = CapturingLLM('{"route":"direct"}')
    LLMRouter(with_history_llm).route("什么是 RAG", history=[])
    LLMRouter(no_history_llm).route("什么是 RAG")

    assert with_history_llm.prompts[-1] == no_history_llm.prompts[-1]


# --------------------------------------------------------------------------- #
# 3. LLMPlanner prompt 注入
# --------------------------------------------------------------------------- #
def test_llm_planner_injects_history_into_prompt():
    llm = CapturingLLM('[{"step_id":1,"description":"检索","tool":"retrieval","query":"RAG"}]')
    planner = LLMPlanner(llm)
    route = RouteDecision(
        route=RouteType.COMPLEX_TASK,
        need_retrieval=True,
        need_planning=True,
        reason="complex",
    )

    planner.plan("继续梳理它", route, history=HISTORY)

    prompt = llm.prompts[-1]
    assert "RAG 综述" in prompt
    assert "继续梳理它" in prompt


# --------------------------------------------------------------------------- #
# 4. AnswerGenerator 两条路径注入
# --------------------------------------------------------------------------- #
def test_answer_generator_injects_history_in_direct_path():
    llm = CapturingLLM("RAG 主要解决知识更新滞后的问题。")
    gen = LLMAnswerGenerator(llm)
    state = AgentState(question="它主要解决什么问题？", history=HISTORY)
    state.route = RouteDecision(route=RouteType.DIRECT, need_retrieval=False, reason="direct")

    answer = gen.generate(state)

    prompt = llm.prompts[-1]
    assert answer != ""
    assert "RAG 综述" in prompt
    assert "它主要解决什么问题？" in prompt


def test_answer_generator_injects_history_in_grounded_path():
    llm = CapturingLLM("根据证据 [1]，RAG 解决知识滞后。")
    gen = LLMAnswerGenerator(llm)
    state = AgentState(question="它主要解决什么问题？", history=HISTORY)
    state.route = RouteDecision(
        route=RouteType.DOCUMENT_RETRIEVAL, need_retrieval=True, reason="doc"
    )
    state.evidence = [
        Evidence(
            chunk_id="c1", document_id="d1", file_name="Lec04.pdf", page_number=12,
            text="RAG 通过检索外部知识缓解知识滞后", score=0.9,
            retrieval_method=RetrievalMethod.RRF,
        )
    ]

    gen.generate(state)

    prompt = llm.prompts[-1]
    assert "RAG 综述" in prompt
    assert "证据" in prompt
    assert "它主要解决什么问题？" in prompt


def test_answer_generator_prompt_identical_when_no_history():
    with_llm = CapturingLLM("ok")
    no_llm = CapturingLLM("ok")
    gen_with = LLMAnswerGenerator(with_llm)
    gen_no = LLMAnswerGenerator(no_llm)

    s1 = AgentState(question="什么是 RAG", history=[])
    s2 = AgentState(question="什么是 RAG")
    for s in (s1, s2):
        s.route = RouteDecision(route=RouteType.DIRECT, need_retrieval=False, reason="direct")
    gen_with.generate(s1)
    gen_no.generate(s2)

    assert with_llm.prompts[-1] == no_llm.prompts[-1]


# --------------------------------------------------------------------------- #
# 5. 规则路由/Mock 忽略历史（关键词不被污染）
# --------------------------------------------------------------------------- #
def test_rule_router_ignores_history_for_classification():
    router = RuleBasedRouter()
    history_with_complex_words = [
        turn(0, "请写一份比较报告", "这是历史里的复杂任务词")
    ]

    plain = router.route("什么是 attention", has_documents=True)
    with_hist = router.route(
        "什么是 attention", has_documents=True, history=history_with_complex_words
    )

    assert plain.route == with_hist.route == RouteType.DIRECT
    assert plain.need_retrieval == with_hist.need_retrieval


def test_mock_router_ignores_history():
    from app.core.mocks import MockRouter

    router = MockRouter()
    plain = router.route("什么是 attention", has_documents=True)
    with_hist = router.route(
        "什么是 attention", has_documents=True,
        history=[turn(0, "写一份对比报告", "历史")],
    )
    assert plain.route == with_hist.route


# --------------------------------------------------------------------------- #
# 6. 预算：超长历史不爆 prompt
# --------------------------------------------------------------------------- #
def test_long_history_is_bounded_in_prompt():
    llm = CapturingLLM('{"route":"direct"}')
    router = LLMRouter(llm)
    long_history = [turn(i, f"问题{i}" * 200, f"回答{i}" * 400) for i in range(30)]

    router.route("当前问题", history=long_history)

    prompt = llm.prompts[-1]
    assert len(prompt) < 4000, "历史必须受字符预算约束"
    assert "当前问题" in prompt


# --------------------------------------------------------------------------- #
# 7. 指代消解端到端（真实组件 + 假 LLM 验证 prompt 携带可消解上下文）
# --------------------------------------------------------------------------- #
def test_coreference_context_reaches_answer_generator():
    """验收场景：'我在做 RAG 综述' → '它主要解决什么问题？'

    本测试不依赖真实模型的语义理解（那属于手动验收），
    而是确保生成 prompt 里同时具备：①历史主题 RAG ②当前带指代的问题。
    模型据此即可把"它"解析为 RAG。
    """
    llm = CapturingLLM("RAG 主要解决大模型知识陈旧和幻觉问题。")
    gen = LLMAnswerGenerator(llm)
    state = AgentState(
        question="它主要解决什么问题？",
        history=[turn(0, "我在做 RAG 综述", "了解，RAG 是检索增强生成。")],
    )
    state.route = RouteDecision(route=RouteType.DIRECT, need_retrieval=False, reason="direct")

    gen.generate(state)

    prompt = llm.prompts[-1]
    assert "RAG" in prompt
    assert "它" in prompt
    assert "历史对话" in prompt
