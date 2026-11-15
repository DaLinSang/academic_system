"""Conservative rules for choosing useful long-term memories from a turn."""

from __future__ import annotations

import re

from app.core.types import MemoryCandidate, MemoryItem, MemoryTier


class RuleBasedMemoryManager:
    """Save explicit, useful statements; do not save the assistant's answer."""

    _RULES = (
        (MemoryTier.PREFERENCE, ("我偏好", "我喜欢", "以后请", "以后都", "不要用")),
        (MemoryTier.PROFILE, ("我的专业是", "我研究", "我在读", "我是", "我就读")),
        (MemoryTier.TASK_STATE, ("我正在", "我在做", "还没完成", "下一步要", "目前项目")),
    )
    _SENSITIVE = re.compile(
        r"\b\d{11}\b|\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b|"
        r"密码|验证码|身份证|银行卡|API[_ -]?KEY|TOKEN",
        re.IGNORECASE,
    )
    _GENERAL_REPLY_REQUEST = re.compile(
        r"^请用(?:中文|英文|中英双语)(?:回答|回复)(?:我)?$"
    )

    def extract_candidates(
        self,
        question: str,
        answer: str,
        existing_memories: list[MemoryItem] | None = None,
    ) -> list[MemoryCandidate]:
        """Inspect this turn; an explicit useful statement can be saved immediately."""
        del answer  # Assistant-generated content is not a reliable user fact.
        known = {
            (item.tier, self._normalize(item.content)) for item in existing_memories or []
        }
        candidates = []
        for statement in re.split(r"[。！？\n;；]", question):
            content = statement.strip()
            if not 5 <= len(content) <= 200 or self._SENSITIVE.search(content):
                continue
            if self._GENERAL_REPLY_REQUEST.fullmatch(content):
                key = (MemoryTier.PREFERENCE, self._normalize(content))
                if key not in known:
                    candidates.append(
                        MemoryCandidate(
                            tier=MemoryTier.PREFERENCE,
                            content=content,
                            importance=0.7,
                            reason="Explicit general reply preference",
                        )
                    )
                    known.add(key)
                continue
            for tier, markers in self._RULES:
                if any(marker in content for marker in markers):
                    key = (tier, self._normalize(content))
                    if key not in known:
                        candidates.append(
                            MemoryCandidate(
                                tier=tier,
                                content=content,
                                importance=0.7 if tier != MemoryTier.TASK_STATE else 0.6,
                                reason="Explicit user statement useful in future turns",
                            )
                        )
                        known.add(key)
                    break
        return candidates

    @staticmethod
    def _normalize(content: str) -> str:
        return "".join(content.casefold().split())
