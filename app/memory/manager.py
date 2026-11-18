"""长期记忆的写入决策（A 负责，策略层）。

分工边界
--------
    MemoryManager（本文件，A）  判断「这段对话里有没有值得长期保存的信息」
                                 → 产出 MemoryCandidate，不碰数据库
    MemoryStore（成员 B）       负责「怎么存、怎么查、怎么删」
                                 → 消费 MemoryCandidate，落成 MemoryItem

**本模块绝不直接写数据库。** 它只回答"存不存、存什么"，存储是 B 的事。

为什么需要三层门禁
------------------
Proposal 4.3 要求"只有稳定、对未来有用、安全的信息才存，原始对话默认不存"。
如果每轮都往记忆库里塞东西，会有两个后果：
  1. 记忆库被噪音淹没，检索命中率下降（RQ2 直接受污染）；
  2. 用户的临时情绪、一次性请求被当成长期事实，回答越来越偏。

所以本实现设三道闸：

    闸 1  关键词预筛   —— 没有"我偏好/我在做/以后都"这类信号，直接返回 []，
                          不调模型。绝大多数对话轮次都卡在这里，省 token。
    闸 2  LLM 抽取     —— 让模型判断稳定性与有用性，输出结构化候选。
    闸 3  后处理过滤   —— 敏感信息、与已有记忆重复、格式非法的，一律丢弃。

第 3 道闸不能省：模型很乐意把手机号、学号原样写进 content，
而这些东西一旦进库，后续所有检索结果都会带着它。

给 RQ2 的观测数据
-----------------
`last_stats` 记录了每轮的漏斗：模型提议几条、被哪道闸拦掉几条。
报告里可以直接用它算"记忆写入拒绝率"，比只报最终条数有说服力。
"""

from __future__ import annotations

import re
from typing import Any

from app.core.interfaces import LLM
from app.core.json_utils import extract_json_array
from app.core.types import MemoryCandidate, MemoryItem, MemoryTier, Usage

__all__ = ["LLMMemoryManager", "find_sensitive", "looks_memorable"]

# --------------------------------------------------------------------------- #
# 闸 1：关键词预筛
# --------------------------------------------------------------------------- #
# 只放**强信号**。"我" 单独出现太常见（"帮我看看"），放进来会让几乎每轮都调模型。
# 长信号：出现在句中任意位置都算（短语足够特异，误命中概率低）
_MEMORY_SIGNALS = (
    # 偏好 / 对未来回答方式的要求
    "我偏好", "我喜欢", "我习惯", "我一直", "我目前", "我们现在",
    "以后都", "之后都", "以后用", "以后回答", "以后请", "往后都", "从现在起",
    "每次回答", "回答时请", "回答之前", "回答前请", "请先说明", "请记住", "别忘了",
    "下次",
    # 背景
    "我在读", "我在做", "我正在", "我研究", "我的项目", "我的方向", "我们组",
    # 任务进度
    "还没完成", "已经完成", "下一步", "正在写", "正在做",
)

# 短信号：只在句首或分句边界匹配。
# Issue #31：此前 "我是" 用裸子串匹配，在「告诉我是自己查阅生成的」里误命中，
# 导致一次性请求也被当成记忆信号，白白消耗一次模型调用。
_MEMORY_SIGNALS_BOUNDARY = ("我是", "记住", "别忘")
_CLAUSE_BOUNDARY = "，。；、！？ \n\t"

_ENGLISH_SIGNALS = (
    "i prefer", "i'm working on", "i am working on", "my project", "my research",
    "remember that", "from now on", "always use", "never use", "next time",
    # Issue #31：英文的"每次回答/回答前说明"偏好
    "every time", "before answering", "in every answer", "always respond",
    "please remember", "from now on please", "i want you to always",
)

# --------------------------------------------------------------------------- #
# 闸 3：敏感信息
# --------------------------------------------------------------------------- #
_SENSITIVE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("手机号", re.compile(r"1[3-9]\d{9}")),
    ("邮箱", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")),
    ("身份证", re.compile(r"\d{17}[\dXx]")),
    ("银行卡", re.compile(r"\b\d{16,19}\b")),
    ("API key", re.compile(r"(sk|ghp|gho)-[A-Za-z0-9]{12,}")),
)

_SENSITIVE_KEYWORDS = ("密码", "口令", "身份证", "银行卡", "住址", "家庭住址", "学号", "工号")

_MAX_CONTENT_CHARS = 120

_SYSTEM_PROMPT = """你是学术助手的记忆管理器。判断这段对话里有没有值得长期保存的稳定信息，输出 json 数组。

三层记忆：
- profile：用户背景（专业、研究方向、身份、已修课程）
- preference：回答偏好（语言、长度、格式、详细程度、**对未来回答方式的要求**）
- task_state：任务进度（当前项目、已完成部分、待解决问题）

**重要**：用户要求"以后/每次回答时都要这样做"属于 preference，即使句子形如指令。
只描述本轮怎么做的一次性指令才不保存。

必须同时满足三条才保存：
1. 稳定：下次对话仍然成立。"今天好累"不算，"我在做 RAG 综述"算。
2. 有用：会影响未来的回答方式或内容。
3. 安全：不含手机号、邮箱、身份证、密码、住址、学号、API key 等敏感信息。

不要保存：
- 一次性的请求（"帮我总结这段"）
- 原始对话原文，或你自己刚才的回答
- 临时情绪、寒暄、客套话

content 的要求：一句中文陈述，主语是"用户"，不超过 60 字，不要带引用标记 [1]。
importance：0 到 1 的小数，信息越稳定、越重要就越高。
reason：一句中文，说明为什么值得保存，会写进实验日志。

没有值得保存的内容时，输出空数组 []。

例子（严格遵守）：

用户："以后回答时请先说明是否引用了我的文档"
→ [{"tier": "preference", "content": "用户希望回答时先说明是否引用了上传文档", "importance": 0.9, "reason": "对未来所有回答方式的要求"}]

用户："请你在每次回答问题前加上'好的'"
→ [{"tier": "preference", "content": "用户希望每次回答前先说'好的'", "importance": 0.8, "reason": "稳定的回答格式偏好"}]

用户："我正在写 RAG 报告，实验部分还没有完成，请记住"
→ [{"tier": "task_state", "content": "用户正在写 RAG 报告，实验部分尚未完成", "importance": 0.7, "reason": "当前项目进度"}]

用户："请解释一下 RAG"
→ []

用户："记住我的邮箱是 test@example.com"
→ []

只输出 json 数组，不要输出任何其他内容：
[{"tier": "profile|preference|task_state", "content": "用户...", "importance": 0.0, "reason": ""}]"""


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #
def looks_memorable(question: str) -> bool:
    """闸 1：这句话里有没有值得记忆的强信号。

    长信号任意位置匹配；短信号（"我是"/"记住"）只在句首或标点后匹配，
    避免"告诉我是自己查的"这类句子误命中。
    """
    text = question.strip().casefold()
    if len(text) < 4:
        return False
    if any(signal in text for signal in _MEMORY_SIGNALS):
        return True
    if any(signal in text for signal in _ENGLISH_SIGNALS):
        return True
    for signal in _MEMORY_SIGNALS_BOUNDARY:
        start = 0
        while True:
            index = text.find(signal, start)
            if index == -1:
                break
            if index == 0 or text[index - 1] in _CLAUSE_BOUNDARY:
                return True
            start = index + 1
    return False


def find_sensitive(content: str) -> str | None:
    """闸 3：返回命中的敏感类型名，没命中返回 None。"""
    for name, pattern in _SENSITIVE_PATTERNS:
        if pattern.search(content):
            return name
    for keyword in _SENSITIVE_KEYWORDS:
        if keyword in content:
            return keyword
    return None


def _normalise_content(content: Any) -> str | None:
    """清洗 content：去引用标记、压空白、限长。不合法返回 None。"""
    if not isinstance(content, str):
        return None
    text = re.sub(r"\[\d+\]", "", content)
    text = re.sub(r"\s+", " ", text).strip(" -·•\t")
    if len(text) < 6:
        return None
    if len(text) > _MAX_CONTENT_CHARS:
        text = text[: _MAX_CONTENT_CHARS - 1].rstrip() + "…"
    return text


def _as_tier(value: Any) -> MemoryTier | None:
    key = str(value).strip().lower()
    for tier in MemoryTier:
        if key == tier.value:
            return tier
    return None


def _as_importance(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, score))


def _bigrams(text: str) -> set[str]:
    clean = re.sub(r"[\s，。；：、,.!?！？]", "", text)
    if len(clean) < 2:
        return {clean} if clean else set()
    return {clean[i : i + 2] for i in range(len(clean) - 1)}


def _is_duplicate(candidate: str, existing: list[str], threshold: float = 0.8) -> bool:
    """字符二元组 Jaccard 相似度。中文不需要分词，bigram 足够稳。"""
    target = _bigrams(candidate)
    if not target:
        return False
    for other in existing:
        other_set = _bigrams(other)
        if not other_set:
            continue
        overlap = len(target & other_set) / len(target | other_set)
        if overlap >= threshold:
            return True
    return False


def _format_existing(memories: list[MemoryItem] | None, limit: int = 10) -> str:
    if not memories:
        return "（无）"
    lines = [f"- [{m.tier.value}] {m.content}" for m in memories[:limit]]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 记忆管理器
# --------------------------------------------------------------------------- #
class LLMMemoryManager:
    """用一次 LLM 调用判断"该不该存"，实现 `app.core.interfaces.MemoryManager`。"""

    def __init__(
        self,
        llm: LLM,
        temperature: float = 0.0,
        max_candidates: int = 3,
        duplicate_threshold: float = 0.8,
    ) -> None:
        if max_candidates < 1:
            raise ValueError("max_candidates 至少为 1")
        if not 0 < duplicate_threshold <= 1:
            raise ValueError("duplicate_threshold 必须落在 (0, 1]")
        self.llm = llm
        self.temperature = temperature
        self.max_candidates = max_candidates
        self.duplicate_threshold = duplicate_threshold
        # 与 Router / Planner 同一约定：组件自己调模型，就把用量挂在 last_usage 上
        self.last_usage = Usage()
        self.last_stats: dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    def extract_candidates(
        self,
        question: str,
        answer: str,
        existing_memories: list[MemoryItem] | None = None,
    ) -> list[MemoryCandidate]:
        self.last_usage = Usage()
        self.last_stats = {
            "llm_called": False,
            "proposed": 0,
            "accepted": 0,
            "dropped_sensitive": 0,
            "dropped_duplicate": 0,
            "dropped_invalid": 0,
            "skipped": None,
        }

        # 闸 1：没有强信号就不花这次模型调用
        if not looks_memorable(question):
            self.last_stats["skipped"] = "no_signal"
            return []

        user_prompt = (
            f"已有记忆（不要重复保存）：\n{_format_existing(existing_memories)}\n\n"
            f"用户这一轮说的话：\n{question}\n\n"
            f"助手这一轮的回答（仅供参考，不要保存回答本身）：\n{answer[:500]}"
        )

        try:
            response = self.llm.generate(
                [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=self.temperature,
                json_mode=True,
            )
            self.last_usage = response.usage
        except Exception as exc:  # noqa: BLE001 - 记忆写入失败不能影响回答
            self.last_stats["skipped"] = f"llm_error:{type(exc).__name__}"
            return []

        self.last_stats["llm_called"] = True

        try:
            payload = extract_json_array(response.text)
        except Exception:  # noqa: BLE001
            self.last_stats["skipped"] = "parse_error"
            return []

        if not payload:
            return []

        self.last_stats["proposed"] = len(payload)
        return self._filter(payload, existing_memories)

    # ------------------------------------------------------------------ #
    # 闸 3：后处理
    # ------------------------------------------------------------------ #
    def _filter(
        self,
        payload: list[Any],
        existing_memories: list[MemoryItem] | None,
    ) -> list[MemoryCandidate]:
        accepted: list[MemoryCandidate] = []
        seen: list[str] = [m.content for m in (existing_memories or [])]

        for item in payload:
            if len(accepted) >= self.max_candidates:
                break
            if not isinstance(item, dict):
                self.last_stats["dropped_invalid"] += 1
                continue

            tier = _as_tier(item.get("tier"))
            content = _normalise_content(item.get("content"))
            if tier is None or content is None:
                self.last_stats["dropped_invalid"] += 1
                continue

            sensitive = find_sensitive(content)
            if sensitive is not None:
                # 直接丢弃而不是打码：打码后的记忆仍然可能误导后续回答
                self.last_stats["dropped_sensitive"] += 1
                continue

            if _is_duplicate(content, seen, self.duplicate_threshold):
                self.last_stats["dropped_duplicate"] += 1
                continue

            accepted.append(
                MemoryCandidate(
                    tier=tier,
                    content=content,
                    importance=_as_importance(item.get("importance")),
                    reason=str(item.get("reason") or "").strip()[:120],
                )
            )
            seen.append(content)

        self.last_stats["accepted"] = len(accepted)
        return accepted
