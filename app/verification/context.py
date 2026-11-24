"""Non-citation context sources used during claim verification.

These sources may support user-specific statements, but they must never be
presented as academic PDF or web citations. The frozen ``Evidence`` contract
therefore remains unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.core.types import ConversationTurn


class VerificationSourceType(StrEnum):
    USER_PROMPT = "user_prompt"
    CONVERSATION = "conversation"
    LONG_TERM_MEMORY = "long_term_memory"


@dataclass(frozen=True, slots=True)
class VerificationSource:
    source_id: str
    source_type: VerificationSourceType
    text: str
    priority: int
    academic_citation: bool = False


@dataclass(frozen=True, slots=True)
class VerificationContext:
    sources: tuple[VerificationSource, ...] = ()

    def texts(self) -> list[str]:
        """Return source text in conflict-resolution priority order."""
        return [source.text for source in self.sources]


def build_verification_context(
    question: str,
    *,
    memory_context: list[str] | tuple[str, ...] = (),
    history: list[ConversationTurn] | tuple[ConversationTurn, ...] = (),
) -> VerificationContext:
    """Build non-academic verification context without changing Evidence.

    The current prompt has the highest priority, then recent conversation turns,
    then long-term memory. Empty strings are ignored.
    """
    sources: list[VerificationSource] = []
    if question.strip():
        sources.append(
            VerificationSource(
                source_id="prompt:current",
                source_type=VerificationSourceType.USER_PROMPT,
                text=question.strip(),
                priority=300,
            )
        )
    for index, turn in enumerate(reversed(history)):
        text = f"用户：{turn.question}\n助手：{turn.answer}".strip()
        if text:
            sources.append(
                VerificationSource(
                    source_id=f"conversation:{turn.turn_index}:{index}",
                    source_type=VerificationSourceType.CONVERSATION,
                    text=text,
                    priority=200 - index,
                )
            )
    for index, memory in enumerate(memory_context):
        if memory.strip():
            sources.append(
                VerificationSource(
                    source_id=f"memory:{index}",
                    source_type=VerificationSourceType.LONG_TERM_MEMORY,
                    text=memory.strip(),
                    priority=100 - index,
                )
            )
    return VerificationContext(tuple(sorted(sources, key=lambda item: -item.priority)))
