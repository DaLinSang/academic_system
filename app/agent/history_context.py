"""多轮历史上下文格式化（A 负责）。

唯一职责：把 ``list[ConversationTurn]`` 转成可安全拼进 LLM prompt 的文本，
并施加**字符预算**，防止历史无限增长吃掉上下文窗口与 RQ4 成本。

设计要点（Issue #44）：
  - 只取最近 N 轮：越早的对话相关性越低，且更容易误导当前问题。
  - 每轮问答单独截断：避免某一轮的超长回答独占预算。
  - 全局字符封顶：即使轮数很多，总长度也有硬上限。
  - 明确标记"历史"与"当前问题"：降低模型把历史答案当成事实证据的风险。

为什么用字符预算而不是精确 token：
  项目里没有 tiktoken 之类的分词器，引入新依赖不划算。
  中文 1 字约 1–2 token、英文 1 字符约 0.25 token，
  用保守的字符上限足以防止上下文爆炸；精确计数留给后续。

本模块是纯函数，不发网络请求、不读全局状态，便于单测。
"""

from __future__ import annotations

from app.core.types import ConversationTurn

__all__ = [
    "DEFAULT_MAX_TURNS",
    "DEFAULT_MAX_CHARS_PER_TURN",
    "DEFAULT_MAX_TOTAL_CHARS",
    "format_history",
]

DEFAULT_MAX_TURNS = 5
"""默认保留最近多少轮。超过部分直接丢弃。"""

DEFAULT_MAX_CHARS_PER_TURN = 400
"""单轮（问题+回答合计）的字符上限。"""

DEFAULT_MAX_TOTAL_CHARS = 2000
"""整段历史文本的字符硬上限。"""


def _clip(text: str, limit: int) -> str:
    """截断到 limit 字符并加省略号。limit<=0 时返回空。"""
    if limit <= 0 or not text:
        return ""
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: max(1, limit - 1)].rstrip() + "…"


def format_history(
    history: list[ConversationTurn] | None,
    *,
    max_turns: int = DEFAULT_MAX_TURNS,
    max_chars_per_turn: int = DEFAULT_MAX_CHARS_PER_TURN,
    max_total_chars: int = DEFAULT_MAX_TOTAL_CHARS,
) -> str:
    """把历史对话格式化成 prompt 片段。

    Args:
        history: 历史轮次；None 或空列表返回空字符串（单轮行为不变）。
        max_turns: 只保留最近这些轮（按 turn_index 升序输出）。
        max_chars_per_turn: 单轮问题+回答合计的字符上限。
        max_total_chars: 输出总字符硬上限（含标题与标记）。

    Returns:
        形如"【历史对话】…【/历史对话】"的文本，可直接拼进 user prompt；
        没有历史时返回 ""，调用方据此判断是否注入。
    """
    if not history or max_turns <= 0 or max_total_chars <= 0:
        return ""

    # 只取最近 max_turns 轮，再按 turn_index 恢复时间顺序
    recent = sorted(history, key=lambda t: t.turn_index)[-max_turns:]

    header = "【历史对话（供理解『它/这个』等指代，不是事实证据）】"
    footer = "【/历史对话】"
    budget = max_total_chars - len(header) - len(footer) - 2
    if budget <= 0:
        return ""

    # 从最近往回取：预算不够时优先保留最近的轮，早期轮先被牺牲
    chosen: list[ConversationTurn] = []
    used = 0
    for turn in reversed(recent):
        q_limit = max_chars_per_turn // 3
        a_limit = max_chars_per_turn - q_limit
        question = _clip(turn.question, q_limit)
        answer = _clip(turn.answer, a_limit)
        block_len = len(f"用户：{question}\n助手：{answer}") + 2
        if used + block_len > budget:
            continue
        chosen.append(turn)
        used += block_len

    chosen.reverse()  # 恢复时间顺序：早的轮在前、当前问题在后
    blocks = []
    for turn in chosen:
        q_limit = max_chars_per_turn // 3
        question = _clip(turn.question, q_limit)
        answer = _clip(turn.answer, max_chars_per_turn - q_limit)
        blocks.append(f"用户：{question}\n助手：{answer}")

    if not blocks:
        return ""

    body = "\n\n".join(blocks)
    # 早的轮在前、当前问题在后，符合阅读顺序
    return f"{header}\n{body}\n{footer}"
