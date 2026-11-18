"""用户会话存储（Issue #45，A 负责）。

**与 RunLogger 的边界**（不要把两者混成一个东西）：

| | RunLogger | SessionStore |
|---|---|---|
| 用途 | 实验记录 | 用户聊天历史 |
| 目录 | `runs/<run_id>/` | `runs/sessions/<user>/<session>.jsonl` |
| 粒度 | 一次实验的 4 模式 × N 次 | 一个用户的一个会话 |
| 谁写 | 评测脚本显式调用 | UI 每轮调用 |
| 恢复 | 不需要 | 需要按 session 读回 |

安全约束：
- `user_id` / `session_id` 做白名单清洗，防止 `../` 路径穿越；
- 写盘前递归剔除 key / token / password / secret 等字段；
- 不写 `.env`、不写请求头、不写完整 LLM 响应原文。
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.types import AgentState

__all__ = ["SessionStore", "SessionSummary", "TurnRecord"]

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# 精确键名。绝不能用 "token" 做子串匹配——那会把 total_tokens /
# prompt_tokens 这些成本指标一起删掉，RQ4 就没数据了。
_SECRET_KEYS = {
    "api_key", "apikey", "token", "access_token", "refresh_token",
    "password", "secret", "authorization", "credential", "private_key",
}
_SECRET_VALUE = re.compile(
    r"(sk-[A-Za-z0-9_-]{6,})|(Bearer\s+[A-Za-z0-9._-]{4,})|"
    r"((?:api[_-]?key|token|password|secret)\s*[=:]\s*)[A-Za-z0-9._-]{6,}",
    re.IGNORECASE,
)


class TurnRecord(dict[str, Any]):
    """一轮对话的落盘记录。用 dict 而非 Pydantic，方便 C 直接读成 JSON。"""


class SessionSummary(dict[str, Any]):
    """会话列表里的一条摘要。"""


def _safe_id(value: str, label: str) -> str:
    if not _SAFE_ID.fullmatch(value or ""):
        raise ValueError(
            f"非法 {label}: {value!r}，只允许字母/数字/下划线/连字符，长度 1-64"
        )
    return value


def _scrub(value: Any) -> Any:
    """递归剔除敏感键，并脱敏文本里的密钥值。

    注意用精确键名匹配：用 "token" 做子串会把 total_tokens / prompt_tokens
    一起删掉，RQ4 的成本统计就没数据了。
    """
    if isinstance(value, dict):
        return {
            key: _scrub(item)
            for key, item in value.items()
            if str(key).casefold() not in _SECRET_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_scrub(item) for item in value]
    if isinstance(value, str):
        return _SECRET_VALUE.sub("[已脱敏]", value)
    return value


class SessionStore:
    """按 user_id / session_id 存聊天轮次到本地 JSONL。"""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or (settings.runtime.runs_dir / "sessions")

    # ------------------------------------------------------------------ #
    def _path(self, user_id: str, session_id: str) -> Path:
        user = _safe_id(user_id, "user_id")
        session = _safe_id(session_id, "session_id")
        return self.base_dir / user / f"{session}.jsonl"

    # ------------------------------------------------------------------ #
    def append(
        self,
        state: AgentState,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> Path:
        """追加一轮。user/session 缺省时用 state 里的值。"""
        uid = user_id if user_id is not None else state.user_id
        sid = session_id if session_id is not None else state.session_id
        path = self._path(uid, sid)
        path.parent.mkdir(parents=True, exist_ok=True)

        # C review #51：UI 的 _render_state / presenters 依赖 evidence、citations、
        # claims、route、trace 和四个开关。只存 count 会让重启后引用与证据定位
        # 永久丢失，无法满足 #32 验收第 5/8 点。
        snapshot = _scrub(json.loads(state.model_dump_json()))
        record = TurnRecord(
            turn_index=state.turn_index,
            question=state.question,
            draft_answer=state.draft_answer,
            final_answer=state.final_answer,
            started_at=state.started_at.isoformat(),
            saved_at=datetime.now(UTC).isoformat(),
            session_id=state.session_id,
            user_id=state.user_id,
            route=snapshot["route"],
            plan=snapshot["plan"],
            evidence=snapshot["evidence"],
            evidence_count=len(state.evidence),
            citations=snapshot["citations"],
            citation_count=len(state.citations),
            claims=snapshot["claims"],
            usage=snapshot["usage"],
            errors=snapshot["errors"],
            trace=snapshot["trace"],
            enable_routing=state.enable_routing,
            enable_retrieval=state.enable_retrieval,
            enable_memory=state.enable_memory,
            enable_verification=state.enable_verification,
        )

        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_scrub(record), ensure_ascii=False) + "\n")
        return path

    # ------------------------------------------------------------------ #
    def load(self, user_id: str, session_id: str) -> list[TurnRecord]:
        """按写入顺序读回一个会话；不存在时返回空列表。"""
        path = self._path(user_id, session_id)
        if not path.is_file():
            return []
        return [
            TurnRecord(json.loads(line))
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    # ------------------------------------------------------------------ #
    def restore(self, user_id: str, session_id: str) -> list[AgentState]:
        """把历史轮次还原成 AgentState，供 UI 用同一套 presenter 渲染。

        C 的 `_render_state()` 原本就吃 AgentState；有了这个方法，重启后
        引用、claim 证据定位和来源状态都能按同一口径显示，不需要另写 ViewModel。
        `saved_at` 是存储元数据，不属于 AgentState，会被剔除。
        """
        restored: list[AgentState] = []
        # 这些是存储层元数据，不属于 AgentState（_Base 禁止额外字段）
        storage_only = {"saved_at", "evidence_count", "citation_count"}
        for record in self.load(user_id, session_id):
            payload = {k: v for k, v in record.items() if k not in storage_only}
            restored.append(AgentState.model_validate(payload))
        return restored

    # ------------------------------------------------------------------ #
    def list_sessions(self, user_id: str) -> list[SessionSummary]:
        """列出某用户的所有会话，按最后写入时间倒序。"""
        user_dir = self.base_dir / _safe_id(user_id, "user_id")
        if not user_dir.is_dir():
            return []

        summaries: list[SessionSummary] = []
        for path in user_dir.glob("*.jsonl"):
            lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            if not lines:
                continue
            last = json.loads(lines[-1])
            summaries.append(
                SessionSummary(
                    session_id=path.stem,
                    turn_count=len(lines),
                    last_at=last.get("saved_at"),
                    last_question=last.get("question", "")[:60],
                )
            )
        return sorted(summaries, key=lambda s: s.get("last_at") or "", reverse=True)

    # ------------------------------------------------------------------ #
    def clear(self, user_id: str, session_id: str) -> bool:
        """删除一个会话。返回是否真的删掉了文件。"""
        path = self._path(user_id, session_id)
        if not path.is_file():
            return False
        path.unlink()
        return True
