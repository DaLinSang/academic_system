"""单篇 PDF 全文/分层总结（方案 §3 / §8.1，A 负责）。

B 通过 `Retriever.read_document(file_name)` 提供带 `[Page N]` 标签的持久化全文。
本模块负责"怎么把全文变成总结"：

- 小文档（默认 ≤ 50,000 字符）：一次性全文总结；
- 大文档：按页边界切成若干段，逐段生成结构化摘要，再合并为最终总结。
  这样 rag_paper.pdf（实测 60,617 字符）这类论文不会因为超过单次上限而
  退化成 Top-K 证据路径——那正是方案 P0 要修复的缺陷。

边界：
- 只输出回答正文，不输出来源列表/校验结果/运行信息（方案 §5）；
- 不编造页码：只有段落里明确带 `[Page N]` 时才允许正文写"第 N 页"；
- 所有 LLM 调用的 usage 累加到 state.usage（RQ4 成本统计）；
- 任一段失败不抛异常，记 trace 后降级。
"""

from __future__ import annotations

import re

from app.core.interfaces import LLM
from app.core.types import AgentState, Usage

__all__ = ["summarize_document", "split_pages_into_segments"]

_PAGE_RE = re.compile(r"^\[Page (\d+)\]\s*$", re.M)
DEFAULT_SEGMENT_CHARS = 40_000
DEFAULT_SMALL_DOC_CHARS = 50_000

_SYSTEM_PROMPT = """你是学术知识助手。只依据提供的文档原文回答，不要引入外部知识。
只输出回答正文（中文），不要输出来源列表、校验结果、运行日志或后续建议。
文中的 [Page N] 是原始 PDF 页码；只有能明确定位事实时才可写"第 N 页"，无法定位时不要编造页码。
不要生成 [1] 形式的检索引用编号。文档没有说明的事情要明确说资料不足。"""

_STRUCTURE_HINT = "请覆盖：文档主题、背景/目标、方法或结构、主要内容、结论和局限。"


def split_pages_into_segments(
    text: str, max_chars: int = DEFAULT_SEGMENT_CHARS
) -> list[str]:
    """按 `[Page N]` 边界把全文切成不超长、且尽量不跨页的段。

    返回每段文本（仍保留页标签）。超长单页单独成段——宁可段大也不切断页。
    """
    if not text:
        return []
    parts = _PAGE_RE.split(text)
    # parts = [preamble, page_no, page_text, page_no, page_text, ...]
    pages: list[str] = []
    if parts[0].strip():
        pages.append(parts[0].strip())
    for i in range(1, len(parts) - 1, 2):
        page_no, body = parts[i], parts[i + 1]
        pages.append(f"[Page {page_no}]\n{body.strip()}")

    segments: list[str] = []
    current: list[str] = []
    current_len = 0
    for page in pages:
        if current and current_len + len(page) > max_chars:
            segments.append("\n\n".join(current))
            current, current_len = [], 0
        current.append(page)
        current_len += len(page) + 2
    if current:
        segments.append("\n\n".join(current))
    return segments


def summarize_document(
    llm: LLM,
    state: AgentState,
    *,
    small_doc_chars: int = DEFAULT_SMALL_DOC_CHARS,
    segment_chars: int = DEFAULT_SEGMENT_CHARS,
) -> str:
    """按文档大小选择一次性总结或分层总结；失败时返回可解释的降级说明。"""
    text = state.document_text
    if not text:
        return ""
    file_name = state.document_file_name or "文档"

    # C review：全文总结也要带上用户记忆/偏好（如"偏好中文""结论附页码"），
    # 否则 AgentState 注入在这些路径上实际是断的。
    memory_hint = ""
    if state.memory_context:
        lines = [f"- {m.content}" for m in state.memory_context[:8]]
        memory_hint = (
            "\n\n用户长期偏好（仅用于调整表达形式，不能当作文档事实）：\n"
            + "\n".join(lines)
        )

    if len(text) <= small_doc_chars:
        state.log("document_summary", "mode", mode="full", chars=len(text))
        return _summarize_once(
            llm, state, text + memory_hint, file_name,
            _SYSTEM_PROMPT + "\n" + _STRUCTURE_HINT,
        )

    segments = split_pages_into_segments(text, max_chars=segment_chars)
    state.log(
        "document_summary", "mode",
        mode="hierarchical", chars=len(text), segments=len(segments),
    )
    section_summaries: list[str] = []
    for idx, segment in enumerate(segments, start=1):
        prompt = (
            f"下面是文档《{file_name}》的第 {idx}/{len(segments)} 部分原文。"
            "请提炼这部分的主要内容，保留可定位的页码标记（如第 N 页），"
            "不要输出来源列表或运行信息。\n\n" + segment
        )
        result = _summarize_once(llm, state, prompt, file_name, _SYSTEM_PROMPT)
        if result.startswith("（摘要失败"):
            state.log("document_summary", "segment_error", segment=idx)
        section_summaries.append(f"【第 {idx} 部分】\n{result}")

    merged = "\n\n".join(section_summaries)
    final_prompt = (
        f"下面是文档《{file_name}》分段摘要（覆盖全文）。请综合成一份结构完整的中文总结，"
        "避免重复，保留可定位的页码信息，只输出正文。\n\n" + merged
        + f"\n\n用户问题：{state.question}"
    )
    return _summarize_once(
        llm, state, final_prompt + memory_hint, file_name,
        _SYSTEM_PROMPT + "\n" + _STRUCTURE_HINT,
    )


def _summarize_once(llm: LLM, state: AgentState, prompt: str, file_name: str, system: str) -> str:
    try:
        response = llm.generate(
            [{"role": "system", "content": system},
             {"role": "user", "content": prompt}],
            temperature=0.0,
        )
    except Exception as exc:  # noqa: BLE001 - 总结失败不能打断回答
        state.log("generate", "error", error=type(exc).__name__)
        return f"（摘要失败：{type(exc).__name__}）"
    state.usage = state.usage.add(response.usage or Usage())
    return (response.text or "").strip() or "（模型没有返回有效内容）"
