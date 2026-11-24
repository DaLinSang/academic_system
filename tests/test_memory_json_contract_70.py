"""Issue #70：Real 模型 JSON 对象/数组契约冲突回归。

根因：json_mode=json_object 强制模型输出对象，但旧解析器只认顶层数组，
     导致 MemoryManager parse_error → stored=0，模型却先说了"已记录"。

本文件守：
1. 统一协议 {"memories": [...]} 正确解析（含空数组）
2. 兼容单项合法对象与旧版顶层数组（向后兼容）
3. 拒绝任意对象、非法 JSON，且失败可诊断（parse_detail 记录类别）
4. 敏感信息不入库；用量统计不漏调用
5. 模型声称"已记录"但实际 stored=0 时，最终答案追加纠正说明
"""

from __future__ import annotations

from app.agent.orchestrator import Orchestrator
from app.core.types import AgentState, LLMResponse, TraceEvent, Usage
from app.memory.manager import LLMMemoryManager, MemoryParseError, _parse_memory_payload


class FakeLLM:
    def __init__(self, text: str, *, error: Exception | None = None) -> None:
        self.text = text
        self.error = error
        self.calls = 0
        self.last_json_mode = False

    def generate(self, messages, **kwargs):
        self.calls += 1
        self.last_json_mode = bool(kwargs.get("json_mode"))
        if self.error:
            raise self.error
        return LLMResponse(text=self.text, usage=Usage(total_tokens=42), model="fake")


def mgr_with(text):
    llm = FakeLLM(text)
    return LLMMemoryManager(llm), llm


QUESTION = "请记住：我偏好中文、简短的要点回答。"


# --------------------------------------------------------------------------- #
# 1. 统一协议与兼容
# --------------------------------------------------------------------------- #
def test_object_wrapped_memories_parsed():
    """Issue #70 首选协议：{"memories": [...]}。"""
    text = '{"memories":[{"tier":"preference","content":"用户偏好中文简短回答","importance":0.9,"reason":"稳定偏好"}]}'
    mgr, llm = mgr_with(text)
    got = mgr.extract_candidates(QUESTION, "好的")
    assert len(got) == 1
    assert "中文" in got[0].content
    assert llm.last_json_mode is True
    assert mgr.last_usage.total_tokens == 42


def test_empty_memories_is_valid():
    text = '{"memories":[]}'
    mgr, _ = mgr_with(text)
    assert mgr.extract_candidates(QUESTION, "好的") == []
    assert mgr.last_stats["skipped"] is None


def test_single_bare_object_accepted():
    """兼容模型实际常见的单项对象输出。"""
    text = '{"tier":"preference","content":"用户偏好简短回答","importance":0.8,"reason":"稳定"}'
    mgr, _ = mgr_with(text)
    got = mgr.extract_candidates(QUESTION, "好的")
    assert len(got) == 1


def test_legacy_top_level_array_still_works():
    """向后兼容旧版提示词输出的顶层数组（多条不能丢）。"""
    text = ('[{"tier":"preference","content":"用户偏好中文","importance":0.8,"reason":"a"},'
            '{"tier":"profile","content":"用户是港大学生","importance":0.9,"reason":"b"}]')
    mgr, _ = mgr_with(text)
    assert len(mgr.extract_candidates(QUESTION, "好的")) == 2


def test_fenced_object_wrapped():
    text = '好的：\n```json\n{"memories":[{"tier":"preference","content":"用户偏好中文","importance":0.8,"reason":"a"}]}\n```'
    mgr, _ = mgr_with(text)
    assert len(mgr.extract_candidates(QUESTION, "好的")) == 1


# --------------------------------------------------------------------------- #
# 2. 严格拒绝 + 可诊断
# --------------------------------------------------------------------------- #
def test_arbitrary_object_rejected_with_diagnostic():
    """{"tier" 和 "content" 都没有} 不是记忆，不能被当候选。"""
    mgr, _ = mgr_with('{"foo":"bar"}')
    assert mgr.extract_candidates(QUESTION, "好的") == []
    assert mgr.last_stats["skipped"] == "parse_error"
    assert mgr.last_stats["parse_detail"] == "object_without_memories"


def test_memories_not_array_diagnostic():
    with __import__("pytest").raises(MemoryParseError) as e:
        _parse_memory_payload('{"memories":{"tier":"preference"}}')
    assert e.value.kind == "memories_not_array"


def test_invalid_json_diagnostic():
    mgr, _ = mgr_with("这不是 json")
    assert mgr.extract_candidates(QUESTION, "好的") == []
    assert mgr.last_stats["parse_detail"].startswith("not_json")


def test_model_error_counts_usage_and_diagnoses():
    """模型调用失败：返回空、记 llm_error、不泄漏原始内容。"""
    llm = FakeLLM("", error=RuntimeError("boom"))
    mgr = LLMMemoryManager(llm)
    assert mgr.extract_candidates(QUESTION, "好的") == []
    assert mgr.last_stats["skipped"] == "llm_error:RuntimeError"
    assert llm.calls == 1


# --------------------------------------------------------------------------- #
# 3. 安全 + 纠正说明
# --------------------------------------------------------------------------- #
def test_sensitive_memory_still_dropped():
    text = ('{"memories":[{"tier":"profile","content":"用户邮箱是 test@example.com",'
            '"importance":0.9,"reason":"x"}]}')
    mgr, _ = mgr_with(text)
    assert mgr.extract_candidates(QUESTION, "好的") == []
    assert mgr.last_stats["dropped_sensitive"] >= 1


def test_claimed_saved_but_stored_zero_adds_correction():
    """模型说"已记录"但实际 stored=0 → 最终答案追加纠正说明。"""
    state = AgentState(question=QUESTION, draft_answer="好的，已记录你的偏好。")
    state.trace.append(TraceEvent(
        stage="memory_write", event="end", detail={"stored": 0}
    ))
    state.trace.append(TraceEvent(
        stage="memory_write", event="stats",
        detail={"skipped": "parse_error", "parse_detail": "object_without_memories"},
    ))
    note = Orchestrator._memory_write_note(state)
    assert "未保存" in note
    assert "parse_error" in note


def test_claimed_recorded_with_inserted_object_still_adds_correction():
    state = AgentState(
        question=QUESTION,
        draft_answer="好的，我已将这项回答偏好记录为长期记忆。",
    )
    state.trace.append(TraceEvent(
        stage="memory_write", event="end", detail={"stored": 0}
    ))
    state.trace.append(TraceEvent(
        stage="memory_write", event="stats", detail={"skipped": "no_signal"}
    ))

    assert "实际未保存" in Orchestrator._memory_write_note(state)


def test_successful_write_adds_no_correction():
    state = AgentState(question=QUESTION, draft_answer="好的，已记录。")
    state.trace.append(TraceEvent(
        stage="memory_write", event="end", detail={"stored": 1}
    ))
    assert Orchestrator._memory_write_note(state) == ""


def test_no_claim_no_correction():
    state = AgentState(question=QUESTION, draft_answer="这是关于 RAG 的解释。")
    state.trace.append(TraceEvent(
        stage="memory_write", event="end", detail={"stored": 0}
    ))
    assert Orchestrator._memory_write_note(state) == ""
