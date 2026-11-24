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
from app.core.json_utils import extract_json_array, extract_json_object
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
    "每次回答", "回答时请", "回答之前", "回答前请", "以后在回答",
    "请先说明", "请记住", "别忘了", "长期记忆", "作为长期记忆",
    "下次",
    # Issue #82：否定式长期偏好（"不要每次都…"）。必须带频率/未来词，
    # 避免"我不要这个答案"这类一次性否定误命中。
    "不要每次", "不要总", "不要动不动", "不用每次", "不需要每次",
    "以后别", "以后不要", "后面别", "后面不要", "往后别", "从现在起不要",
    "别每次", "别再", "不要再", "别总",
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
    # Issue #82：否定式偏好（必须带频率/未来词，避免一次性否定误命中）
    "don't always", "dont always", "do not always", "no longer",
    "don't keep", "dont keep", "never again", "from now on don't",
    "stop including", "stop adding", "stop mentioning",
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

_SYSTEM_PROMPT = """你是学术助手的记忆管理器。判断这段对话里有没有值得长期保存的稳定信息，输出 json 对象。

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

没有值得保存的内容时，输出 {"memories": []}。

例子（严格遵守）：

用户："以后回答时请先说明是否引用了我的文档"
→ {"memories": [{"tier": "preference", "content": "用户希望回答时先说明是否引用了上传文档", "importance": 0.9, "reason": "对未来所有回答方式的要求"}]}

用户："请你在每次回答问题前加上'好的'"
→ {"memories": [{"tier": "preference", "content": "用户希望每次回答前先说'好的'", "importance": 0.8, "reason": "稳定的回答格式偏好"}]}

用户："我正在写 RAG 报告，实验部分还没有完成，请记住"
→ {"memories": [{"tier": "task_state", "content": "用户正在写 RAG 报告，实验部分尚未完成", "importance": 0.7, "reason": "当前项目进度"}]}

用户："请解释一下 RAG"
→ {"memories": []}

用户："记住我的邮箱是 test@example.com"
→ {"memories": []}

只输出 json 对象，不要输出任何其他内容：
{"memories": [{"tier": "profile|preference|task_state", "content": "用户...", "importance": 0.0, "reason": ""}]}"""


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #

class MemoryParseError(ValueError):
    """结构化输出无法解析成合法候选。带简短类别，便于写进 stats 而不泄漏原文。"""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        super().__init__(kind)


def _parse_memory_payload(text: str) -> list[Any]:
    """把模型的结构化输出解析成候选列表（Issue #70）。

    统一协议是 ``{"memories": [...]}``（与 json_mode=json_object 一致），
    同时兼容实际模型可能的两种变体，但不把任意对象当记忆：
      - 顶层数组 ``[...]``（旧版提示词的输出）
      - 单项合法对象 ``{"tier": ..., "content": ...}``
    空结果 ``{"memories": []}`` → []；其他对象 → MemoryParseError。
    """
    stripped = (text or "").strip()
    # 剥掉 ```json ... ``` 围栏（可能带"好的："前缀）后再判断顶层类型。
    # json_utils 内部也会剥，但这里需要先知道顶层是 [ 还是 { 才能正确分流。
    fence = re.search(r"```(?:json)?\s*(.*?)```", stripped, re.DOTALL | re.I)
    if fence:
        stripped = fence.group(1).strip()
    if not stripped:
        raise MemoryParseError("empty_output")

    # 按首字符分流：extract_json_object 会从数组文本里抠出第一个对象，
    # 所以必须先判断顶层类型，否则顶层数组会被误当成单项对象丢掉后续项。
    if stripped.startswith("["):
        try:
            return extract_json_array(stripped)
        except Exception as exc:
            raise MemoryParseError(f"not_json:{type(exc).__name__}") from exc

    # 对象：首选 {"memories": [...]}；兼容单项合法对象；拒绝任意对象
    try:
        obj = extract_json_object(stripped)
    except Exception as exc:
        raise MemoryParseError(f"not_json:{type(exc).__name__}") from exc
    if "memories" in obj:
        items = obj["memories"]
        if not isinstance(items, list):
            raise MemoryParseError("memories_not_array")
        return items
    if "tier" in obj or "content" in obj:
        return [obj]
    raise MemoryParseError("object_without_memories")


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


# Issue #97：矛盾判定的系统提示词。只输出 yes/no，避免解析复杂结构。
_CONTRADICTION_SYSTEM_PROMPT = """判断两条长期记忆是否表达了互相矛盾的用户偏好。

只回答 yes 或 no，不要解释。
- yes：两条对同一件事给出相反的稳定偏好（如"每次都附带 RAG" vs "不再附带 RAG"、
  "偏好中文" vs "偏好英文"）。后一条通常是用户对前一条的更新/反悔。
- no：两条只是不同主题、互相补充、或程度不同但不冲突
  （如"喜欢 RAG"和"在做 RAG 项目"、"偏好简短"和"偏好中文"）。
保守判断：不确定就回答 no，绝不把不矛盾的记忆误判为矛盾。"""


def _find_contradiction(
    llm: LLM,
    candidate: str,
    tier: MemoryTier,
    existing: list[MemoryItem],
    temperature: float = 0.0,
    usage_sink: list[Usage] | None = None,
) -> MemoryItem | None:
    """返回与新候选矛盾的同 tier 旧记忆；无矛盾或判定失败返回 None。

    保守设计（Issue #97）：
    - 只比对同 tier，跨 tier 不判矛盾
    - 一次 LLM 调用批量给出判定，失败/解析异常一律返回 None（不误删旧记忆）
    - 提示词要求"不确定答 no"，宁可漏更新也不错更新
    """
    same_tier = [m for m in existing if m.tier == tier and m.memory_id]
    if not same_tier:
        return None
    lines = "\n".join(f"{i}. {m.content}" for i, m in enumerate(same_tier, start=1))
    prompt = (
        f"新偏好：{candidate}\n\n已有同类型记忆：\n{lines}\n\n"
        f"逐条回答新偏好是否与每条已有记忆矛盾。格式：\n"
        f"1. yes/no\n2. yes/no\n（只输出编号和 yes/no）"
    )
    try:
        response = llm.generate(
            [
                {"role": "system", "content": _CONTRADICTION_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=temperature,
            max_tokens=64,
        )
    except Exception:  # noqa: BLE001 - 判定失败就当不矛盾，绝不误删
        return None
    # Issue #97 review：这次判定调用也要计入 RQ4，不能丢弃 usage
    if usage_sink is not None:
        usage_sink.append(response.usage)
    # 逐行找 "N. yes"，只认第一个明确的 yes（保守：一条候选最多替换一条旧记忆）
    for line in (response.text or "").splitlines():
        match = re.match(r"\s*(\d+)\s*[.、:：]?\s*(yes|no)\b", line, re.I)
        if match and match.group(2).casefold() == "yes":
            index = int(match.group(1)) - 1
            if 0 <= index < len(same_tier):
                return same_tier[index]
    return None


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
        # Issue #97：矛盾记忆更新计划 [(memory_id, new_content, importance), ...]
        # 只记录"要更新哪条"，不直接改库——写入仍由 Orchestrator 调 MemoryStore 完成。
        self.last_updates: list[tuple[str, str, float]] = []

    # ------------------------------------------------------------------ #
    def extract_candidates(
        self,
        question: str,
        answer: str,
        existing_memories: list[MemoryItem] | None = None,
    ) -> list[MemoryCandidate]:
        self.last_usage = Usage()
        self.last_updates = []
        self.last_stats = {
            "llm_called": False,
            "proposed": 0,
            "accepted": 0,
            "dropped_sensitive": 0,
            "dropped_duplicate": 0,
            "dropped_invalid": 0,
            "skipped": None,
            "parse_detail": None,
            "contradictions_checked": 0,
            "contradictions_updated": 0,
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
            payload = _parse_memory_payload(response.text)
        except MemoryParseError as exc:
            # Issue #70：保留 parse_error 兼容旧测试/UI，另记可诊断类别，不写原始文本
            self.last_stats["skipped"] = "parse_error"
            self.last_stats["parse_detail"] = exc.kind
            return []
        except Exception as exc:  # noqa: BLE001 - 兜底，不泄漏模型输出
            self.last_stats["skipped"] = "parse_error"
            self.last_stats["parse_detail"] = type(exc).__name__
            return []

        if not payload:
            return []

        self.last_stats["proposed"] = len(payload)
        accepted = self._filter(payload, existing_memories)

        # Issue #97：对通过的候选做矛盾检测。命中的转成"更新旧记忆"计划，
        # 不返回给 add 路径（否则新旧两条都写进去，正是要修的问题）。
        self.last_updates = []
        self.last_stats["contradictions_checked"] = 0
        self.last_stats["contradictions_updated"] = 0
        if accepted and existing_memories:
            to_add: list[MemoryCandidate] = []
            verdict_usages: list[Usage] = []
            for candidate in accepted:
                old = _find_contradiction(
                    self.llm, candidate.content, candidate.tier,
                    existing_memories, self.temperature,
                    usage_sink=verdict_usages,
                )
                self.last_stats["contradictions_checked"] += 1
                if old is not None:
                    # 用新候选覆盖旧记忆：保留旧 tier，用新 content/importance
                    self.last_updates.append((old.memory_id, candidate.content, candidate.importance))
                    self.last_stats["contradictions_updated"] += 1
                else:
                    to_add.append(candidate)
            # Issue #97 review：把每次矛盾判定的 token/call 累加进 last_usage，
            # 否则 Orchestrator 只吸收抽取那一次，RQ4 系统性低估。
            for usage in verdict_usages:
                self.last_usage = self.last_usage.add(usage)
            return to_add
        return accepted

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
