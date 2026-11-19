"""阶段 0 的 Mock 实现（A 负责）。

用途：在 B 的检索、C 的 Verifier 还没写出来之前，让整条链路先跑通。

**Mock 是接口的可执行规格，不是临时垃圾代码。**
  - 返回的数据结构必须与真实实现完全一致；
  - 行为要尽量贴近真实（例如检索没命中就返回空列表，而不是报错）；
  - B / C 的真实实现完成后，本文件保留用于测试和离线演示，不要删。

替换方式：在 Orchestrator 构造时传入真实实现即可，调用方代码不用改。
"""

from __future__ import annotations

import re
import time
import uuid

from app.core.types import (
    AgentState,
    ClaimCheck,
    ClaimStatus,
    ConversationTurn,
    Evidence,
    LLMResponse,
    MemoryCandidate,
    MemoryItem,
    MemoryTier,
    PlanStep,
    RetrievalMethod,
    RouteDecision,
    RouteType,
    StepStatus,
    Usage,
    utcnow,
)

# --------------------------------------------------------------------------- #
# 假语料：模拟三门课讲义里的段落
# --------------------------------------------------------------------------- #
_FAKE_CORPUS: list[dict] = [
    {
        "chunk_id": "lec04:p7:c0",
        "document_id": "lec04",
        "file_name": "COMP7608B_Lec04.pdf",
        "page_number": 7,
        "text": (
            "Retrieval-Augmented Generation 将参数化记忆（模型权重）与非参数化记忆"
            "（外部文档索引）结合。生成前先检索 top-k 段落，把检索结果拼进 prompt。"
        ),
    },
    {
        "chunk_id": "lec04:p9:c1",
        "document_id": "lec04",
        "file_name": "COMP7608B_Lec04.pdf",
        "page_number": 9,
        "text": (
            "Self-RAG 训练模型输出 reflection token，用于判断是否需要检索、"
            "以及检索到的段落是否相关，从而实现自适应检索。"
        ),
    },
    {
        "chunk_id": "lec06:p12:c2",
        "document_id": "lec06",
        "file_name": "COMP7608B_Lecture_06.pdf",
        "page_number": 12,
        "text": (
            "ReAct 让模型交替产生推理轨迹与动作，动作结果作为观察回填上下文，"
            "形成 思考-行动-观察 的循环。"
        ),
    },
]


# --------------------------------------------------------------------------- #
# A 的 Mock
# --------------------------------------------------------------------------- #
class MockLLM:
    """模拟模型调用，返回固定文本并伪造 token 用量。

    真实实现见 app/llm/client.py（阶段 1）。两者签名完全一致。
    """

    def __init__(self, canned: str | None = None) -> None:
        self.canned = canned or (
            "根据讲义，RAG 在生成前会先检索外部文档 [1]。"
            "Self-RAG 进一步让模型自己决定何时检索 [2]。"
            "该方法在三个数据集上取得了最优结果。"
        )
        self.calls = 0

    def generate(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        t0 = time.perf_counter()
        self.calls += 1
        prompt_chars = sum(len(m.get("content", "")) for m in messages)
        # 粗略按 4 字符 ≈ 1 token 估算
        prompt_tokens = max(1, prompt_chars // 4)
        completion_tokens = max(1, len(self.canned) // 4)
        latency_ms = (time.perf_counter() - t0) * 1000

        return LLMResponse(
            text=self.canned,
            model="mock-model",
            finish_reason="stop",
            usage=Usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                cost_usd=0.0,
                llm_calls=1,
                latency_ms=round(latency_ms, 2),
            ),
        )


class MockRouter:
    """关键词启发式路由。真实版本（阶段 2）改用 LLM 分类。

    虽然是 Mock，但故意做成"真的能分对大部分情况"，
    这样阶段 1 的端到端 Demo 演示起来不像玩具。
    """

    _COMPLEX = ("比较", "对比", "综述", "研究计划", "写一份", "报告", "梳理", "多个", "几篇")
    _RETRIEVAL = ("这篇", "文档", "论文", "讲义", "第几页", "哪一页", "提到", "定义", "什么是")

    def route(
        self,
        question: str,
        has_documents: bool = True,
        memory_context: list[MemoryItem] | None = None,
        history: list[ConversationTurn] | None = None,
    ) -> RouteDecision:
        # Mock 路由与规则路由一致：忽略历史，避免污染关键词分类
        q = question.strip()
        if any(k in q for k in self._COMPLEX):
            return RouteDecision(
                route=RouteType.COMPLEX_TASK,
                need_retrieval=has_documents,
                need_memory=bool(memory_context),
                need_planning=True,
                reason="检测到比较/生成类任务，需要多步拆解",
                confidence=0.7,
            )
        if has_documents and any(k in q for k in self._RETRIEVAL):
            return RouteDecision(
                route=RouteType.DOCUMENT_RETRIEVAL,
                need_retrieval=True,
                need_memory=bool(memory_context),
                reason="问题指向用户文档，需要检索",
                confidence=0.75,
            )
        return RouteDecision(
            route=RouteType.DIRECT,
            need_retrieval=False,
            need_memory=bool(memory_context),
            reason="常识性问题，可直接回答",
            confidence=0.6,
        )


class MockPlanner:
    """固定四步计划。真实版本（阶段 2）按问题动态生成。"""

    def plan(
        self,
        question: str,
        route: RouteDecision,
        memory_context: list[MemoryItem] | None = None,
        history: list[ConversationTurn] | None = None,
    ) -> list[PlanStep]:
        if route.route != RouteType.COMPLEX_TASK:
            return []

        return [
            PlanStep(
                step_id=1,
                description="定位与问题相关的文档与页码",
                tool="retrieval",
                query=question,
                status=StepStatus.DONE,
                result_summary="mock: 命中 3 个片段",
            ),
            PlanStep(
                step_id=2,
                description="提取各文档的关键论点",
                tool="llm",
                depends_on=[1],
                status=StepStatus.DONE,
            ),
            PlanStep(
                step_id=3,
                description="按指定维度对比差异",
                tool="llm",
                depends_on=[2],
                status=StepStatus.DONE,
            ),
            PlanStep(
                step_id=4,
                description="生成带页码引用的结论",
                tool="llm",
                depends_on=[3],
                status=StepStatus.DONE,
            ),
        ]


class MockAnswerGenerator:
    """把 evidence 拼成带 [n] 引用的回答。

    真实版本（阶段 1）会调用 LLM，并把编号后的 evidence 作为上下文传入。
    这里刻意保留了 direct 分支：没有证据时应当走模型自身知识，
    而不是一律拒答 —— 这正是 RQ1 想区分的两种情况。
    """

    def __init__(self, llm: MockLLM | None = None) -> None:
        self.llm = llm or MockLLM()

    def generate(self, state: AgentState) -> str:
        # direct 路径：不检索，直接用模型知识回答
        if state.route is not None and state.route.is_direct:
            resp = self.llm.generate(
                [{"role": "user", "content": state.question}], temperature=0.0
            )
            state.usage = state.usage.add(resp.usage)
            return resp.text

        if not state.evidence:
            return "我没有在已上传的文档中找到相关证据，因此无法回答这个问题。"

        lines = ["根据检索到的文档："]
        for i, ev in enumerate(state.evidence, start=1):
            snippet = re.sub(r"\s+", " ", ev.text).strip()
            lines.append(f"- {snippet} [{i}]（{ev.file_name} 第 {ev.page_number} 页）")
        lines.append("以上结论均可在上述页码中核对。")
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# B 的 Mock
# --------------------------------------------------------------------------- #
class MockRetriever:
    """按关键词重合度从假语料里挑 top-k。真实版本见 app/retrieval/。"""

    def retrieve(self, query: str, top_k: int = 5) -> list[Evidence]:
        tokens = set(re.findall(r"\w+", query.lower()))
        scored: list[tuple[float, dict]] = []
        for row in _FAKE_CORPUS:
            text = row["text"].lower()
            hit = sum(1 for t in tokens if t and t in text)
            score = hit / (len(tokens) or 1)
            scored.append((score, row))

        scored.sort(key=lambda x: x[0], reverse=True)
        picked = [row for score, row in scored[:top_k] if score > 0]

        # 关键词一个都没命中时，退化为返回第一条 —— 模拟"向量检索总能返回点东西"
        if not picked:
            picked = [scored[0][1]]

        return [
            Evidence(
                chunk_id=row["chunk_id"],
                document_id=row["document_id"],
                file_name=row["file_name"],
                page_number=row["page_number"],
                text=row["text"],
                score=round(1.0 - i * 0.1, 3),
                retrieval_method=RetrievalMethod.MOCK,
            )
            for i, row in enumerate(picked)
        ]

    def index_document(self, pdf_path: str, document_id: str | None = None) -> int:
        return len(_FAKE_CORPUS)


class MockMemoryStore:
    """内存版记忆存储。真实版本（阶段 2）由 B 用 PostgreSQL 实现。"""

    def __init__(self) -> None:
        self._items: dict[str, MemoryItem] = {}
        self._seed()

    def _seed(self) -> None:
        seed = MemoryItem(
            memory_id="mem-seed-1",
            tier=MemoryTier.PREFERENCE,
            content="用户偏好中文回答，并希望结论附页码引用。",
            importance=0.8,
            user_id="default",
        )
        self._items[seed.memory_id] = seed

    def add(self, candidate: MemoryCandidate, user_id: str = "default") -> MemoryItem:
        item = MemoryItem(
            memory_id=f"mem-{uuid.uuid4().hex[:8]}",
            tier=candidate.tier,
            content=candidate.content,
            importance=candidate.importance,
            user_id=user_id,
        )
        self._items[item.memory_id] = item
        return item

    def search(
        self,
        query: str,
        user_id: str = "default",
        tiers: list[MemoryTier] | None = None,
        top_k: int = 5,
    ) -> list[MemoryItem]:
        items = [m for m in self._items.values() if m.user_id == user_id]
        if tiers:
            items = [m for m in items if m.tier in tiers]
        return sorted(items, key=lambda m: m.importance, reverse=True)[:top_k]

    def update(
        self,
        memory_id: str,
        content: str | None = None,
        importance: float | None = None,
        *,
        user_id: str = "default",
    ) -> MemoryItem | None:
        item = self._items.get(memory_id)
        if item is None or item.user_id != user_id:
            return None
        if content is not None:
            item.content = content
        if importance is not None:
            item.importance = importance
        item.updated_at = utcnow()
        return item

    def delete(self, memory_id: str, *, user_id: str = "default") -> bool:
        item = self._items.get(memory_id)
        if item is None or item.user_id != user_id:
            return False
        del self._items[memory_id]
        return True

    def list_all(self, user_id: str = "default") -> list[MemoryItem]:
        return [m for m in self._items.values() if m.user_id == user_id]


class MockMemoryManager:
    """规则版记忆抽取：只抓"我偏好/我叫/我在做"这类稳定陈述。"""

    _PATTERNS = (
        (MemoryTier.PREFERENCE, ("我偏好", "我喜欢", "以后都", "请用", "不要用")),
        (MemoryTier.PROFILE, ("我是", "我读", "我专业", "我研究")),
        (MemoryTier.TASK_STATE, ("我正在", "我在做", "还没完成", "下一步要")),
    )

    def extract_candidates(
        self,
        question: str,
        answer: str,
        existing_memories: list[MemoryItem] | None = None,
    ) -> list[MemoryCandidate]:
        out: list[MemoryCandidate] = []
        for tier, keys in self._PATTERNS:
            for k in keys:
                if k in question:
                    out.append(
                        MemoryCandidate(
                            tier=tier,
                            content=question.strip()[:120],
                            importance=0.7,
                            reason=f"命中模式「{k}」",
                        )
                    )
                    break
        return out


# --------------------------------------------------------------------------- #
# C 的 Mock
# --------------------------------------------------------------------------- #
class MockVerifier:
    """朴素证据校验：claim 里的词是否在某个 evidence 片段中出现过。

    真实版本（阶段 1–2）由 C 用 LLM 逐条判断。这里刻意做得"能抓出明显无依据的句子"，
    这样 Demo 里能真实演示 unsupported claim 被标红的效果。

    给 C 的提醒：拆分 claim 时**必须过滤掉非事实句**，否则会出现
    "根据检索到的文档："、"以上结论均可核对" 这类句子被判 unsupported，
    把 unsupported 率虚高，直接污染 RQ3 的实验结果。
    """

    _SPLIT = re.compile(r"[。！？\n]+")

    # 非事实句：引导语、结语、纯引用标记、列表符号
    _META = re.compile(
        r"^(根据|依据|以上|综上|下面|如下|参考|注[:：]|说明[:：]|⚠|ℹ|-\s*\[\d+\]|\[\d+\])"
    )

    def _split_claims(self, answer: str) -> list[str]:
        out: list[str] = []
        for raw in self._SPLIT.split(answer):
            s = raw.strip().lstrip("-•*· ").strip()
            if not s:
                continue
            # 去掉引用标记后再看还剩多少"实质内容"
            substantive = re.sub(r"\[\d+\]", "", s)
            substantive = re.sub(r"（[^）]*第\s*\d+\s*页）", "", substantive).strip()
            if len(substantive) < 8:
                continue
            if self._META.match(s):
                continue
            out.append(s)
        return out

    def verify(self, answer: str, evidence: list[Evidence]) -> list[ClaimCheck]:
        sentences = self._split_claims(answer)
        if not sentences:
            return []
        if not evidence:
            return [
                ClaimCheck(
                    claim=s,
                    status=ClaimStatus.UNSUPPORTED,
                    confidence=0.9,
                    note="没有可用的检索证据",
                )
                for s in sentences
            ]

        corpus = " ".join(e.text for e in evidence)
        results: list[ClaimCheck] = []
        for s in sentences:
            # 去掉引用标记再比对
            clean = re.sub(r"\[\d+\]", "", s)
            words = [w for w in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z]{3,}", clean)]
            if not words:
                results.append(
                    ClaimCheck(claim=s, status=ClaimStatus.SUPPORTED, confidence=0.5,
                               supporting_evidence=[evidence[0].chunk_id])
                )
                continue

            hit = [w for w in words if w in corpus]
            ratio = len(hit) / len(words)

            if ratio >= 0.5:
                # 找出具体命中的 chunk，回填 chunk_id 供 UI 定位
                ids = [e.chunk_id for e in evidence if any(w in e.text for w in hit)]
                results.append(
                    ClaimCheck(
                        claim=s,
                        status=ClaimStatus.SUPPORTED,
                        confidence=round(min(0.95, 0.5 + ratio / 2), 2),
                        supporting_evidence=ids or [evidence[0].chunk_id],
                    )
                )
            elif ratio >= 0.2:
                results.append(
                    ClaimCheck(
                        claim=s,
                        status=ClaimStatus.PARTIALLY_SUPPORTED,
                        confidence=round(0.3 + ratio, 2),
                        supporting_evidence=[e.chunk_id for e in evidence if any(w in e.text for w in hit)],
                        note=f"仅 {len(hit)}/{len(words)} 个关键片段在证据中出现",
                    )
                )
            else:
                results.append(
                    ClaimCheck(
                        claim=s,
                        status=ClaimStatus.UNSUPPORTED,
                        confidence=0.85,
                        note="检索证据中找不到对应内容",
                    )
                )
        return results
