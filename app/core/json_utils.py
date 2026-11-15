"""从模型输出里抠出 JSON 的共享工具。

为什么需要它
------------
真实模型经常不听话：前面加一句"好的，分析如下"，外面套 ```json 围栏，
或者 JSON 后面再补一段解释。Router、Planner、MemoryManager 都要吃这种输出，
所以统一放这里，避免三份实现各自漂移。

注意：`app/routing/router.py` 与 `app/planning/planner.py` 目前仍保留各自的
私有实现（阶段 1 写下时还没抽公共层）。迁移它们属于独立重构，不要混在功能 PR 里。
"""

from __future__ import annotations

import json
import re
from typing import Any

__all__ = ["extract_json_array", "extract_json_object"]

_FENCE = re.compile(r"```(?:json)?\s*(.+?)```", re.DOTALL)


def _strip_fence(text: str) -> str:
    match = _FENCE.search(text)
    return match.group(1).strip() if match else text.strip()


def _scan_balanced(text: str, opener: str, closer: str) -> str:
    """从第一个 opener 开始，扫描到配对的 closer。

    必须处理字符串内部的花括号/方括号，否则
    `{"reason": "包含 { 的说明"}` 会被提前截断。
    """
    start = text.find(opener)
    if start == -1:
        raise ValueError(f"模型输出里没有 {opener}")

    depth = 0
    in_string = False
    escaped = False

    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return text[start : index + 1]

    raise ValueError(f"{opener}{closer} 不配对")


def _extract(text: str, opener: str, closer: str, expected: type) -> Any:
    raw = _strip_fence(text)

    # 先直接试一次：模型完全听话时走这条
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, expected):
            return parsed
    except json.JSONDecodeError:
        pass

    # 回退：手工扫描括号平衡的片段
    snippet = _scan_balanced(raw, opener, closer)
    parsed = json.loads(snippet)
    if not isinstance(parsed, expected):
        raise ValueError(f"JSON 顶层不是 {expected.__name__}")
    return parsed


def extract_json_array(text: str) -> list[Any]:
    """抠出第一个 JSON 数组。模型返回对象或纯文本时抛 ValueError。"""
    return _extract(text, "[", "]", list)


def extract_json_object(text: str) -> dict[str, Any]:
    """抠出第一个 JSON 对象。模型返回数组或纯文本时抛 ValueError。"""
    return _extract(text, "{", "}", dict)
