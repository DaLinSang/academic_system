"""Resolve explicit filenames and numbered lecture titles before semantic search.

Resolution is request-local: no remembered user/session focus lives in the retriever.
An OCR field label can disambiguate two otherwise identical lecture titles.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.types import DocumentChunk

_LECTURE = re.compile(r"(?<![a-z0-9])lecture\s*0*(\d+)(?![a-z0-9])", re.I)
_FIELDS = {
    "language": r"(?<![a-z])language(?![a-z])|语言|語言",
    "date": r"(?<![a-z])date(?![a-z])|日期",
    "time": r"(?<![a-z])time(?![a-z])|时间|時間",
    "venue": r"(?<![a-z])venue(?![a-z])|地点|地點|场地|場地",
}


def lecture_numbers(text: str) -> set[int]:
    return {int(m) for m in _LECTURE.findall(text)}


@dataclass(frozen=True)
class DocumentScope:
    names: tuple[str, ...] = ()
    clarification: str | None = None


def resolve_scope(query: str, chunks: list[DocumentChunk]) -> DocumentScope:
    names = sorted({c.file_name for c in chunks})
    explicit = tuple(n for n in names if n.casefold() in query.casefold())
    if explicit:
        return DocumentScope(explicit)
    if re.search(r"[\w.-]+\.pdf\b", query, re.I):
        return DocumentScope(clarification="指定的 PDF 尚未索引，请核对文件名并成功导入后再问。")
    numbers = lecture_numbers(query)
    if not numbers:
        return DocumentScope()
    # Only a title near the beginning of page 1 establishes document identity.
    # A later bibliography or a mention of Lecture 6 is not a Lecture 6 document.
    candidates = sorted({c.file_name for c in chunks
                         if c.page_number == 1 and c.char_start in (None, 0)
                         and numbers <= lecture_numbers(c.text[:240])})
    course = re.findall(r"\b[a-z]{2,}\d{3,}[a-z]?\b", query, re.I)
    if course:
        candidates = [name for name in candidates if any(
            c.file_name == name and c.page_number == 1
            and all(code.casefold() in (c.file_name + ' ' + c.text[:240]).casefold()
                    for code in course) for c in chunks)]
    if len(candidates) > 1:
        fields = [field for field, pattern in _FIELDS.items() if re.search(pattern, query, re.I)]
        if fields:
            # Require all requested structured labels, not a loose word occurrence.
            with_fields = [name for name in candidates if all(any(
                c.file_name == name and re.search(
                    rf"^\s*{field}\s*[/：:]", c.text, re.I | re.M
                ) for c in chunks) for field in fields)]
            if len(with_fields) == 1:
                candidates = with_fields
    if len(candidates) == 1:
        return DocumentScope(tuple(candidates))
    if candidates:
        return DocumentScope(clarification=(
            "多份已导入文档具有相同的 Lecture 编号，无法确定你指的是哪份。"
            "请在问题中指定 PDF 文件名：" + "、".join(candidates)
        ))
    return DocumentScope(clarification=(
        "没有找到能确认该 Lecture 编号的文档标题。请指定 PDF 文件名，"
        "以免使用其他课程或论文的内容回答。"
    ))


def expand_field_query(query: str) -> str:
    """Bridge mixed Chinese/English field queries without translating factual values."""
    query = _LECTURE.sub(lambda m: f"Lecture {int(m[1])}", query)
    fields = [field for field, pattern in _FIELDS.items() if re.search(pattern, query, re.I)]
    return query + " " + " ".join(fields)
