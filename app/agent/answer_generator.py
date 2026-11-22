"""基于检索证据生成带页码引用的回答（A 负责）。

职责边界
--------
**只负责"把证据变成人话"**，不负责检索、不负责校验、不负责编排。

输入：`AgentState`（已填好 `question` / `evidence` / `route` / `memory_context`）
输出：一段带 `[n]` 引用标记的中文回答

三条路径
--------
1. **direct**（无证据）：用模型自身知识回答。这条路径的存在本身很重要——
   RQ1 要区分"该检索"和"不该检索"，如果 direct 也强行检索，这个区分就消失了。
2. **有证据**：把证据编号后拼进 prompt，要求模型只用证据作答，每条事实标引用。
3. **无证据但需要证据**：明确告知"资料不足"，而不是让模型编一个看起来合理的答案。
   这是 Proposal 里 "explicit uncertainty when evidence is missing" 的落地点。

为什么引用编号必须在 prompt 之前就固定
--------------------------------------
Orchestrator 在调用本模块后立即执行 `state.build_citations()`，把 evidence 按顺序
编成 `[1] [2] ...`。如果生成时用的编号和 citations 的编号不一致，用户点引用就会跳错页。
所以这里**只按 `state.evidence` 的当前顺序编号**，不做任何重排。

引用质量如何观测（给 C 的评测用）
--------------------------------
模型可能编出超出范围的编号（比如只有 3 条证据却写 `[7]`）。这类越界引用正是
"citation precision" 的失败样本，**不能静默删掉**，否则评测看不到问题。
因此这里保留原文，只把越界编号记进 `trace`：

    state.log("generate", "citation_audit", used=[1, 3], invalid=[7])
"""

from __future__ import annotations

import re

from app.agent.history_context import format_history
from app.core.interfaces import LLM
from app.core.types import AgentState
from app.retrieval.study_intent import is_study_request

__all__ = ["LLMAnswerGenerator", "NO_EVIDENCE_MESSAGE", "extract_citation_indices"]

NO_EVIDENCE_MESSAGE = (
    "我在已上传的文档里没有检索到与这个问题相关的内容，因此无法给出有依据的回答。\n\n"
    "可能的原因：① 相关文档还没有导入；② 问题里的术语与文档用词差异较大。\n"
    "如果你希望我基于通用知识回答，可以明确说明「不用查文档」。"
)

_CITATION_PATTERN = re.compile(r"\[(\d+)\]")
_PAGE_WITH_CITATION = re.compile(r"第\s*([\d、,，]+)\s*页[ \t]*((?:\[\d+\][ \t]*)+)")

_SYSTEM_PROMPT = """你是学术知识助手。根据给定的证据回答用户问题。

规则：
1. 每一条事实陈述后面必须标注来源编号，格式为 [1]、[2]，编号必须来自给定证据。
2. 只能使用证据中出现的信息。证据里没有的内容不要补充，即使你知道答案。
3. 如果证据不足以回答问题，明确说明"现有资料不足以回答"，并指出缺少什么信息。
4. 如果多个证据互相矛盾，分别陈述各自的说法并指出矛盾，不要合并成一个确定结论。
5. 用中文回答，结构清晰。不要复述这些规则，不要输出"根据规则"之类的话。
6. 不要在开头写"根据检索到的文档"这类引导语，直接给结论。
7. 若提供了历史对话，其中的"它/这个/前者"等指代**必须**从历史对话中解析出具体对象，
   再带着这个对象去理解用户问题；历史只用于消解指代，不是事实来源，事实仍只能取自证据。
   不要把指代词拿去证据里猜——即使证据里有多个候选对象也先查历史。"""

_DIRECT_SYSTEM_PROMPT = """你是学术知识助手。用户的问题不需要检索其个人文档，请直接回答。

规则：
1. 用中文回答，结构清晰。
2. 如果问题涉及你不确定的事实，明确说明不确定性，不要编造。
3. 如果这个问题其实需要用户自己的文档才能回答，在回答末尾提示用户上传相关材料。
4. 不要在开头写引导语，直接给结论。
5. 若提供了历史对话，"它/这个"等指代必须从历史中解析出具体对象后再回答。"""


def extract_citation_indices(text: str) -> list[int]:
    """按出现顺序取出答案里的引用编号（去重）。"""
    seen: list[int] = []
    for match in _CITATION_PATTERN.finditer(text):
        index = int(match.group(1))
        if index not in seen:
            seen.append(index)
    return seen


class LLMAnswerGenerator:
    """实现 `app.core.interfaces.AnswerGenerator`。"""

    def __init__(
        self,
        llm: LLM,
        temperature: float = 0.0,
        max_evidence: int = 6,
        max_chars_per_evidence: int = 700,
    ) -> None:
        if max_evidence < 1:
            raise ValueError("max_evidence 至少为 1")
        if max_chars_per_evidence < 50:
            raise ValueError("max_chars_per_evidence 太小，会导致证据被截得没有信息量")
        self.llm = llm
        self.temperature = temperature
        self.max_evidence = max_evidence
        self.max_chars_per_evidence = max_chars_per_evidence

    # ------------------------------------------------------------------ #
    def generate(self, state: AgentState) -> str:
        clarifications = list(dict.fromkeys(
            r.metadata["clarification"] for r in state.tool_results
            if r.tool_name == "retrieval" and r.metadata.get("clarification")
        ))
        if clarifications:
            state.log("generate", "source_clarification", count=len(clarifications))
            return "\n".join(clarifications)
        if not state.evidence:
            return self._generate_without_evidence(state)
        return self._generate_from_evidence(state)

    # ------------------------------------------------------------------ #
    # 路径 1：direct / 无证据
    # ------------------------------------------------------------------ #
    @staticmethod
    def _web_required_but_failed(state: AgentState) -> bool:
        """本轮明确决定联网、但一条网页证据都没拿到（Issue #71）。

        此时不能让模型凭自身知识编造"根据官网..."的事实性回答——
        联网失败不等于可以退化成直接回答。由 trace 判断，独立于 route。
        """
        decisions = [
            e for e in state.trace
            if e.stage == "web_search" and e.event == "decision"
        ]
        if not decisions or not any(
            e.detail.get("should_search") for e in decisions
        ):
            return False
        # 只要有任何网页证据（url 非空）就不算失败
        return not any(getattr(ev, "url", None) for ev in state.evidence)

    def _generate_without_evidence(self, state: AgentState) -> str:
        if self._web_required_but_failed(state):
            warning = next(
                (
                    e.detail.get("warning")
                    for e in state.trace
                    if e.stage == "web_search" and e.event == "end"
                    and e.detail.get("warning")
                ),
                None,
            )
            state.log("generate", "web_search_failed", warning=warning)
            suffix = f"（原因：{warning}）" if warning else ""
            return (
                "本轮已尝试联网搜索，但没有取得可用的网页结果，"
                f"因此不能提供所要求的官网介绍或真实链接{suffix}。"
                "请检查网络/代理后重试，或稍后再试。"
            )

        is_direct = state.route is not None and state.route.is_direct

        if not is_direct:
            # 需要证据却没检索到：不要编，直接说清楚
            state.log("generate", "no_evidence", route=state.route.route if state.route else None)
            return NO_EVIDENCE_MESSAGE

        try:
            response = self.llm.generate(
                [
                    {"role": "system", "content": _DIRECT_SYSTEM_PROMPT},
                    {"role": "user", "content": self._direct_prompt(state)},
                ],
                temperature=self.temperature,
            )
        except Exception as exc:  # noqa: BLE001 - 生成失败要降级，不能炸掉请求
            state.log("generate", "error", error=f"{type(exc).__name__}: {exc}")
            return f"抱歉，模型调用失败（{type(exc).__name__}），本次无法回答。"

        state.usage = state.usage.add(response.usage)
        return response.text.strip() or "模型没有返回有效内容。"

    # ------------------------------------------------------------------ #
    # 路径 2：有证据
    # ------------------------------------------------------------------ #
    def _generate_from_evidence(self, state: AgentState) -> str:
        try:
            response = self.llm.generate(
                [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": self._grounded_prompt(state)},
                ],
                temperature=self.temperature,
            )
        except Exception as exc:  # noqa: BLE001
            state.log("generate", "error", error=f"{type(exc).__name__}: {exc}")
            return self._fallback_from_evidence(state)

        state.usage = state.usage.add(response.usage)
        answer = response.text.strip()

        if not answer:
            state.log("generate", "empty_response")
            return self._fallback_from_evidence(state)

        answer = self._align_cited_pages(state, answer)
        self._audit_citations(state, answer)
        return answer

    @staticmethod
    def _align_cited_pages(state: AgentState, answer: str) -> str:
        """Keep explicit page numbers consistent with the adjacent citation.

        Citation [n] is an evidence index, not PDF page n. Only a page number
        immediately followed by that citation is unambiguous enough to fix.
        """
        corrections: list[dict[str, object]] = []

        def replace(match: re.Match[str]) -> str:
            citations = [int(value) for value in _CITATION_PATTERN.findall(match.group(2))]
            if not citations or any(not 1 <= n <= len(state.evidence) for n in citations):
                return match.group()
            items = [state.evidence[n - 1] for n in citations]
            if any(item.is_web for item in items):
                return match.group()
            actual_pages = list(dict.fromkeys(item.page_number for item in items))
            stated_pages = [int(value) for value in re.findall(r"\d+", match.group(1))]
            if actual_pages == stated_pages:
                return match.group()
            corrections.append({"citations": citations, "from_pages": stated_pages,
                                "to_pages": actual_pages})
            return f"第 {'、'.join(map(str, actual_pages))} 页 {match.group(2).strip()}"

        aligned = _PAGE_WITH_CITATION.sub(replace, answer)
        if corrections:
            state.log("generate", "page_citation_correction", corrections=corrections)
        return aligned

    # ------------------------------------------------------------------ #
    # 提示词构造
    # ------------------------------------------------------------------ #
    def _format_evidence(self, state: AgentState) -> str:
        blocks: list[str] = []
        for index, item in enumerate(state.evidence[: self.max_evidence], start=1):
            text = re.sub(r"\s+", " ", item.text).strip()
            if len(text) > self.max_chars_per_evidence:
                text = text[: self.max_chars_per_evidence].rstrip() + "…"
            # Issue #71：网页证据必须把真实 URL 送进 prompt，模型才不会编造链接；
            # 文档证据保持"文件名 第 N 页"格式，不影响既有 citation 口径。
            if getattr(item, "url", None):
                label = getattr(item, "title", None) or item.file_name
                blocks.append(f"[{index}] 网页：{label}\nURL：{item.url}\n{text}")
            else:
                blocks.append(
                    f"[{index}] 来源：{item.file_name} 第 {item.page_number} 页\n{text}"
                )
        return "\n\n".join(blocks)

    def _format_preferences(self, state: AgentState) -> str:
        if not state.memory_context:
            return ""
        lines = [f"- {item.content}" for item in state.memory_context[:5]]
        return "\n用户长期偏好（仅影响表达方式，不影响事实内容）：\n" + "\n".join(lines)

    def _format_history(self, state: AgentState) -> str:
        """历史对话片段（Issue #44）。空历史返回空串，不改变既有 prompt。"""
        return format_history(state.history)

    def _grounded_prompt(self, state: AgentState) -> str:
        history = self._format_history(state)
        history_block = f"{history}\n\n" if history else ""
        study_instruction = ""
        if is_study_request(state.question):
            study_instruction = (
                "学习任务：用多个页面的证据归纳不同主题，写成有条理的中文回答；"
                "比较多份文档时分别说明各文档的观点，再列共同点和差异；"
                "如果两份材料并未讨论同一个概念，要明确说明，"
                "只有双方证据都支持时才能写成共同点；"
                "复习请求可按核心概念、方法、结果及自测问题组织。"
                "每条具体结论标对应引用；缺少证据的部分明确说明，"
                "不要声称已经通读未给出的页面。"
                "引用 [n] 是证据编号，不是 PDF 页码；如写出页码，"
                "必须与该证据的真实页码一致。\n\n"
            )
        return (
            f"证据：\n\n{self._format_evidence(state)}\n"
            f"{self._format_preferences(state)}\n\n"
            f"{history_block}{study_instruction}"
            f"用户问题：{state.question}"
        )

    def _direct_prompt(self, state: AgentState) -> str:
        history = self._format_history(state)
        history_block = f"{history}\n\n" if history else ""
        return f"{history_block}{state.question}{self._format_preferences(state)}"

    # ------------------------------------------------------------------ #
    # 引用审计与降级
    # ------------------------------------------------------------------ #
    def _audit_citations(self, state: AgentState, answer: str) -> None:
        """记录引用使用情况，供 C 计算 citation precision / recall。

        越界编号不删除——它本身就是"引用不可靠"的证据，删了评测就看不见了。
        """
        used = extract_citation_indices(answer)
        upper_bound = min(len(state.evidence), self.max_evidence)
        invalid = [index for index in used if index < 1 or index > upper_bound]
        state.log(
            "generate",
            "citation_audit",
            used=used,
            invalid=invalid,
            evidence_count=len(state.evidence),
        )

    def _fallback_from_evidence(self, state: AgentState) -> str:
        """模型不可用时的兜底：把证据原文按页列出。

        这样即使 API 挂了，用户仍能看到"在哪一页有什么内容"，
        Demo 现场不会出现空白页。质量不如模型生成，但可用。
        """
        state.log("generate", "fallback_to_extract")
        lines = ["模型暂时不可用，以下是检索到的原文片段，供你直接查阅：", ""]
        for index, item in enumerate(state.evidence[: self.max_evidence], start=1):
            text = re.sub(r"\s+", " ", item.text).strip()
            if len(text) > 200:
                text = text[:200].rstrip() + "…"
            lines.append(f"[{index}] {item.file_name} 第 {item.page_number} 页：{text}")
        return "\n".join(lines)
