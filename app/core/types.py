"""公共数据结构（Single Source of Truth）。

本模块是 A / B / C 三方共同依赖的契约层，**任何字段变更都必须三人确认**。

设计原则
--------
1. 所有跨模块传递的对象都在这里定义，任何模块不得私自定义重复结构。
2. 每个结构都可序列化为 JSON —— 评测脚本（B1–B4）直接写 JSONL，无需额外转换。
3. 只放数据，不放业务逻辑。检索、验证、路由的算法分别属于 B / C / A 的模块。
4. 字段尽量"够用就好"：新增字段前先问会不会让另外两人返工。

归属速查
--------
DocumentChunk  -> B 产出（PDF 解析 + 切分）
Evidence       -> B 产出（检索结果）
MemoryItem     -> B 产出（MemoryStore 读取）
RouteDecision  -> A 产出（Router）
ClaimCheck     -> C 产出（Verifier）
ToolResult     -> A 产出（Orchestrator 记录工具调用）
AgentState     -> A 产出并贯穿全流程，是唯一的状态载体
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = [
    "RouteType",
    "MemoryTier",
    "ClaimStatus",
    "RetrievalMethod",
    "StepStatus",
    "DocumentChunk",
    "Evidence",
    "RouteDecision",
    "MemoryItem",
    "MemoryCandidate",
    "ClaimCheck",
    "ToolResult",
    "Citation",
    "PlanStep",
    "ConversationTurn",
    "Usage",
    "TraceEvent",
    "LLMResponse",
    "AgentState",
    "utcnow",
]


def utcnow() -> datetime:
    """统一的时间戳来源，避免各处混用 naive / aware datetime。"""
    return datetime.now(UTC)


class _Base(BaseModel):
    """所有结构的基类：允许额外字段留空、枚举按值序列化。"""

    model_config = ConfigDict(extra="forbid", use_enum_values=False)


# --------------------------------------------------------------------------- #
# 枚举
# --------------------------------------------------------------------------- #
class RouteType(StrEnum):
    """Router 的三分类结果。第一版只支持这三种（见分工文档阶段 2）。"""

    DIRECT = "direct"
    """直接回答：常识性、无需外部证据的问题。"""

    DOCUMENT_RETRIEVAL = "document_retrieval"
    """单步检索：答案在用户自己的文档里。"""

    COMPLEX_TASK = "complex_task"
    """多步任务：论文比较、研究计划、报告撰写，需要 Planner 拆解。"""


class MemoryTier(StrEnum):
    """三层记忆（对应 Proposal 4.3）。"""

    PROFILE = "profile"
    """Tier A：用户背景（专业、研究方向、已修课程）。"""

    PREFERENCE = "preference"
    """Tier B：回答偏好（语言、长度、详细程度）。"""

    TASK_STATE = "task_state"
    """Tier C：任务进度（当前项目、已完成部分、待解决问题）。"""


class ClaimStatus(StrEnum):
    """单条 claim 的证据支持状态。"""

    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    UNSUPPORTED = "unsupported"


class RetrievalMethod(StrEnum):
    """Evidence 的来源通道，评测时要按通道拆分统计。"""

    VECTOR = "vector"
    BM25 = "bm25"
    RRF = "rrf"
    """混合排序后的结果。"""

    WEB = "web"
    """网页检索。网页证据没有页码，用 url 定位。"""

    MOCK = "mock"
    """阶段 0 的假数据。"""


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


# --------------------------------------------------------------------------- #
# B 的产出
# --------------------------------------------------------------------------- #
class DocumentChunk(_Base):
    """PDF 切分后的最小检索单元。

    验收要求（B 必须保证）：page_number 绝不能丢，且与原 PDF 一致。
    """

    chunk_id: str = Field(description="全局唯一，建议 f'{document_id}:p{page}:c{idx}'")
    document_id: str
    file_name: str
    page_number: int = Field(ge=1, description="1-based，直接对应 PDF 页码")
    text: str
    char_start: int | None = Field(default=None, description="在整篇文档中的起始字符偏移")
    char_end: int | None = None
    section: str | None = Field(default=None, description="章节标题，能解析到就填")
    token_count: int | None = None
    embedding: list[float] | None = Field(
        default=None, description="768 维（nomic-embed-text）；入库前可为 None"
    )

    @field_validator("text")
    @classmethod
    def _text_not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("chunk.text 不能为空")
        return v


class Evidence(_Base):
    """一条检索证据。AnswerGenerator 和 Verifier 都只吃这个结构。"""

    chunk_id: str
    document_id: str
    file_name: str
    # 网页证据没有页码，允许 0（"无页码"）。文档证据仍应 >= 1。
    page_number: int = Field(default=1, ge=0)
    text: str
    score: float = Field(description="越大越相关；RRF 分数与余弦分数不可直接比较")
    retrieval_method: RetrievalMethod = RetrievalMethod.MOCK
    # 网页来源：url 必填，title 可选。文档证据保持 None。
    url: str | None = Field(default=None, description="网页证据的可访问 URL")
    title: str | None = Field(default=None, description="网页标题，用于 UI 展示")

    @classmethod
    def from_chunk(
        cls,
        chunk: DocumentChunk,
        score: float,
        method: RetrievalMethod = RetrievalMethod.MOCK,
    ) -> Evidence:
        return cls(
            chunk_id=chunk.chunk_id,
            document_id=chunk.document_id,
            file_name=chunk.file_name,
            page_number=chunk.page_number,
            text=chunk.text,
            score=score,
            retrieval_method=method,
        )

    @property
    def is_web(self) -> bool:
        return self.retrieval_method is RetrievalMethod.WEB

    @property
    def locator(self) -> str:
        """人类可读的定位串。文档 'Lec04.pdf p.12'，网页用域名/标题。"""
        if self.is_web:
            label = self.title or self.file_name
            return f"{label} · 网页"
        return f"{self.file_name} p.{self.page_number}"

    @model_validator(mode="after")
    def _check_source_consistency(self) -> Evidence:
        """C review #54：在模型层守住来源不变量，别等到 UI 才暴露。"""
        if self.is_web and not self.url:
            raise ValueError("WEB 证据必须提供 url")
        if not self.is_web and self.page_number < 1:
            raise ValueError("文档证据的 page_number 必须 >= 1（1-based）")
        return self


class MemoryItem(_Base):
    """一条已存储的长期记忆。"""

    memory_id: str
    tier: MemoryTier
    content: str = Field(description="一句话陈述，不要存原始对话")
    importance: float = Field(default=0.5, ge=0.0, le=1.0, description="检索排序用")
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    user_id: str = "default"
    source_turn: int | None = Field(default=None, description="来自第几轮对话，便于溯源")
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryCandidate(_Base):
    """MemoryManager 抽出来的候选记忆，尚未落库。

    A 负责"要不要存"，B 负责"怎么存"。这个结构就是两者的交接点。
    """

    tier: MemoryTier
    content: str
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    reason: str = Field(default="", description="为什么认为它值得保存")


# --------------------------------------------------------------------------- #
# A 的产出
# --------------------------------------------------------------------------- #
class RouteDecision(_Base):
    """Router 的输出。

    注意：need_retrieval 等布尔位与 route 不是冗余关系 ——
    评测 RQ1 时要单独统计"该检索却没检索"和"不该检索却检索了"，
    所以必须能表达 complex_task 内部某一步是否需要检索。
    """

    route: RouteType
    need_retrieval: bool = False
    need_memory: bool = False
    need_planning: bool = False
    need_web_search: bool = False
    reason: str = Field(default="", description="给报告用的可解释理由")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @property
    def is_direct(self) -> bool:
        return self.route == RouteType.DIRECT


class PlanStep(_Base):
    """Planner 拆出的一步。Planner 只生成，不执行。"""

    step_id: int
    description: str
    tool: Literal["retrieval", "memory", "web_search", "llm", "none"] = "none"
    query: str | None = Field(default=None, description="给检索工具用的子查询")
    status: StepStatus = StepStatus.PENDING
    depends_on: list[int] = Field(default_factory=list)
    result_summary: str | None = None


class ToolResult(_Base):
    """任何一次工具调用的记录。用于错误恢复、延迟统计和失败案例分析。"""

    tool_name: str
    success: bool
    output: Any = None
    error: str | None = None
    latency_ms: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class Citation(_Base):
    """最终答案里的一个引用标记，UI 渲染成 [1] [2] 并可跳到对应页。"""

    index: int = Field(ge=1, description="答案中显示的编号，从 1 开始")
    chunk_id: str
    document_id: str
    file_name: str
    page_number: int = Field(ge=0)  # 0 = 网页证据，没有页码
    quote: str = Field(default="", description="被引用的原文片段")
    url: str | None = Field(default=None, description="网页引用的可点击地址")
    title: str | None = Field(default=None, description="网页标题")

    @property
    def is_web(self) -> bool:
        """与 Evidence 用同一来源判定：有 url 即网页。禁止两套口径。"""
        return self.url is not None

    @property
    def locator(self) -> str:
        if self.is_web:
            return f"{self.title or self.file_name} · 网页"
        return f"{self.file_name} p.{self.page_number}"

    @model_validator(mode="after")
    def _check_source_consistency(self) -> Citation:
        if self.is_web and self.page_number != 0:
            raise ValueError("网页引用不能有页码（page_number 必须为 0）")
        if not self.is_web and self.page_number < 1:
            raise ValueError("文档引用 page_number 必须 >= 1")
        return self

    @classmethod
    def from_evidence(cls, index: int, ev: Evidence, quote: str = "") -> Citation:
        return cls(
            index=index,
            chunk_id=ev.chunk_id,
            document_id=ev.document_id,
            file_name=ev.file_name,
            page_number=ev.page_number,
            quote=quote or ev.text[:200],
            url=ev.url,
            title=ev.title,
        )


class Usage(_Base):
    """LLM 用量与成本。RQ4 的唯一数据来源，从第一天就要埋。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    llm_calls: int = 0
    latency_ms: float = 0.0

    def add(self, other: Usage) -> Usage:
        return Usage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            cost_usd=round(self.cost_usd + other.cost_usd, 8),
            llm_calls=self.llm_calls + other.llm_calls,
            latency_ms=round(self.latency_ms + other.latency_ms, 2),
        )


class TraceEvent(_Base):
    """一次可观测事件。失败案例分析和报告里的流程图都靠它。"""

    stage: str = Field(description="router / planner / retrieval / generate / verify ...")
    event: str = Field(description="start / end / error / retry / skip")
    detail: dict[str, Any] = Field(default_factory=dict)
    latency_ms: float = 0.0
    timestamp: datetime = Field(default_factory=utcnow)


class LLMResponse(_Base):
    """LLM 统一调用层的返回。所有模块只认这个，不认 openai 的原始对象。"""

    text: str
    model: str = ""
    usage: Usage = Field(default_factory=Usage)
    finish_reason: str | None = None
    raw: dict[str, Any] | None = Field(default=None, description="原始响应，调试用")

    @property
    def ok(self) -> bool:
        return bool(self.text.strip())


# --------------------------------------------------------------------------- #
# C 的产出
# --------------------------------------------------------------------------- #
class ClaimCheck(_Base):
    """单条 claim 的校验结果。C 的 Verifier 产出。"""

    claim: str
    status: ClaimStatus
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    supporting_evidence: list[str] = Field(
        default_factory=list, description="chunk_id 列表，指回 AgentState.evidence"
    )
    note: str | None = Field(default=None, description="判为 partial / unsupported 的原因")

    @property
    def is_grounded(self) -> bool:
        return self.status == ClaimStatus.SUPPORTED


# --------------------------------------------------------------------------- #
# 多轮对话
# --------------------------------------------------------------------------- #
class ConversationTurn(_Base):
    """历史对话中的一轮。只存问题与最终回答，不存完整 AgentState。"""

    question: str
    answer: str
    turn_index: int = Field(ge=0)
    started_at: datetime = Field(default_factory=utcnow)


# --------------------------------------------------------------------------- #
# 贯穿全流程的状态载体
# --------------------------------------------------------------------------- #
class AgentState(_Base):
    """整条链路的唯一状态容器。

    规则（分工文档阶段 2 明确要求）：
      - 不允许用全局变量传状态；
      - 每个模块只读写自己负责的字段；
      - 新增字段必须同步更新本文件顶部的归属说明。

    B1–B4 的差异**只通过开关字段表达**，不复制四套代码：
      B1 = enable_routing=False, enable_retrieval=False, enable_memory=False, enable_verification=False
      B2 = enable_routing=False, enable_retrieval=True,  enable_memory=False, enable_verification=False
      B3 = enable_routing=True,  enable_retrieval=True,  enable_memory=True,  enable_verification=False
      B4 = 全开
    """

    # ---- 输入 ----
    question: str
    session_id: str = "default"
    user_id: str = "default"
    turn_index: int = 0
    # 上一轮的问答。由调用方传入；为空时行为与此前完全一致（单轮）。
    # 只放简短历史，完整记录由 SessionStore 负责，不塞进 AgentState。
    history: list[ConversationTurn] = Field(default_factory=list)

    # ---- 消融开关（评测用，UI 侧边栏也直接绑这几个）----
    enable_routing: bool = True
    enable_retrieval: bool = True
    enable_memory: bool = True
    enable_verification: bool = True

    # ---- 各阶段产出 ----
    route: RouteDecision | None = None
    plan: list[PlanStep] = Field(default_factory=list)
    current_step: int = 0

    memory_context: list[MemoryItem] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    # B 的全文读取路径：只在单篇文档总结时填充；普通 RAG 仍只使用 evidence。
    document_text: str = ""
    document_file_name: str | None = None
    tool_results: list[ToolResult] = Field(default_factory=list)

    draft_answer: str = ""
    claims: list[ClaimCheck] = Field(default_factory=list)
    final_answer: str = ""
    citations: list[Citation] = Field(default_factory=list)

    # ---- 可观测性 ----
    usage: Usage = Field(default_factory=Usage)
    trace: list[TraceEvent] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=utcnow)

    # ------------------------------------------------------------------ #
    # 便捷方法：只做记录，不含业务判断
    # ------------------------------------------------------------------ #
    def log(self, stage: str, event: str, latency_ms: float = 0.0, **detail: Any) -> None:
        self.trace.append(
            TraceEvent(stage=stage, event=event, detail=detail, latency_ms=latency_ms)
        )

    def record_tool(self, result: ToolResult) -> None:
        self.tool_results.append(result)
        if not result.success and result.error:
            self.errors.append(f"{result.tool_name}: {result.error}")

    def add_evidence(self, items: list[Evidence]) -> None:
        """按 chunk_id 去重后追加，保持检索顺序。"""
        seen = {e.chunk_id for e in self.evidence}
        for ev in items:
            if ev.chunk_id not in seen:
                self.evidence.append(ev)
                seen.add(ev.chunk_id)

    def build_citations(self) -> list[Citation]:
        """把 evidence 顺序编号成 [1] [2] ...，供 AnswerGenerator 引用。"""
        self.citations = [
            Citation.from_evidence(i, ev) for i, ev in enumerate(self.evidence, start=1)
        ]
        return self.citations

    def evidence_by_chunk_id(self) -> dict[str, Evidence]:
        return {e.chunk_id: e for e in self.evidence}

    @property
    def unsupported_claims(self) -> list[ClaimCheck]:
        return [c for c in self.claims if c.status == ClaimStatus.UNSUPPORTED]
