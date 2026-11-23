"""Resolve explicit filenames and numbered lecture titles before semantic search.

Resolution is request-local: no remembered user/session focus lives in the retriever.
An OCR field label can disambiguate two otherwise identical lecture titles.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.types import DocumentChunk

_LECTURE = re.compile(r"(?<![a-z0-9])lec(?:ture)?[\s_-]*0*(\d+)(?![a-z0-9])", re.I)
_COURSE_NUMBER = re.compile(r"(?<![a-z0-9])([a-z]{2,}\d{3,}[a-z]?)[\s_-]+0*(\d+)(?![a-z0-9])", re.I)
_COURSE = re.compile(r"(?<![a-z0-9])[a-z]{2,}\d{3,}[a-z]?(?![a-z0-9])", re.I)
_CHAPTER = re.compile(r"(?<![a-z0-9])chapter[\s_-]*0*(\d+)(?![a-z0-9])", re.I)
_FIELDS = {
    "language": r"(?<![a-z])language(?![a-z])|语言|語言",
    "date": r"(?<![a-z])date(?![a-z])|日期",
    "time": r"(?<![a-z])time(?![a-z])|时间|時間",
    "venue": r"(?<![a-z])venue(?![a-z])|地点|地點|场地|場地",
}


def lecture_numbers(text: str) -> set[int]:
    return {int(m) for m in _LECTURE.findall(text)}


def _course_key(code: str) -> str:
    # Slide filenames sometimes omit the course suffix B used in the question.
    return code.casefold().removesuffix("b")


def _course_matches(query: str, file_name: str, first_page: str) -> bool:
    requested = {_course_key(m.group()) for m in _COURSE.finditer(query)}
    available = {_course_key(m.group()) for m in _COURSE.finditer(file_name + " " + first_page)}
    return not requested or requested <= available


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
    chapter = _CHAPTER.search(query)
    if chapter:
        number = int(chapter.group(1))
        # Keep an optional English chapter topic, e.g. AssociationRule, while
        # ignoring Chinese instructions that follow the document title.
        tail = re.match(r"[\s_-]*([a-z][a-z\s_-]*)", query[chapter.end():], re.I)
        topic = re.sub(r"[^a-z]", "", tail.group(1).casefold()) if tail else ""
        candidates = []
        for name in names:
            match = _CHAPTER.search(name)
            if match and int(match.group(1)) == number:
                stem = re.sub(r"[^a-z]", "", name[match.end():].removesuffix(".pdf").casefold())
                if not topic or topic in stem:
                    candidates.append(name)
        if len(candidates) == 1:
            return DocumentScope((candidates[0],))
        if candidates:
            return DocumentScope(clarification="多个章节文件符合问题，请指定 PDF 文件名：" + "、".join(candidates))
        return DocumentScope(clarification=(
            f"没有找到已索引的 Chapter {number}"
            + (f" {tail.group(1).strip()}" if tail else "")
            + "。请确认上传并建立索引，或指定正确的 PDF 文件名。"
        ))
    numbers = lecture_numbers(query)
    numbers.update(int(match.group(2)) for match in _COURSE_NUMBER.finditer(query))
    if not numbers:
        return DocumentScope()
    first_pages = {c.file_name: c.text[:240] for c in chunks
                   if c.page_number == 1 and c.char_start in (None, 0)}
    # The visible filename is authoritative when it has its own lecture number.
    # A lecture deck can contain an internal "Lecture 1" even when named Lec04.
    filename_matches = sorted(name for name in names
                              if numbers <= lecture_numbers(name)
                              and _course_matches(query, name, first_pages.get(name, "")))
    if filename_matches and _COURSE.search(query):
        if len(filename_matches) == 1:
            return DocumentScope(tuple(filename_matches))
        return DocumentScope(clarification=_ambiguous(filename_matches))
    # Only a title near the beginning of page 1 establishes document identity.
    # A later bibliography or a mention of Lecture 6 is not a Lecture 6 document.
    candidates = sorted({name for name, first_page in first_pages.items()
                         if numbers <= lecture_numbers(first_page)
                         and _course_matches(query, name, first_page)} | set(filename_matches))
    if len(candidates) > 1:
        fields = _FIELDS_IN_QUERY(query)
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
        return DocumentScope(clarification=_ambiguous(candidates))
    return DocumentScope(clarification=(
        "没有找到能确认该 Lecture 编号的文档标题。请指定 PDF 文件名，"
        "以免使用其他课程或论文的内容回答。"
    ))


def _FIELDS_IN_QUERY(query: str) -> list[str]:
    return [field for field, pattern in _FIELDS.items() if re.search(pattern, query, re.I)]


def _ambiguous(candidates: list[str]) -> str:
    return ("多份已导入文档具有相同的 Lecture 编号，无法确定你指的是哪份。"
            "请在问题中指定 PDF 文件名：" + "、".join(candidates))


def expand_field_query(query: str) -> str:
    """Bridge mixed Chinese/English field queries without translating factual values."""
    query = _LECTURE.sub(lambda m: f"Lecture {int(m[1])}", query)
    fields = [field for field, pattern in _FIELDS.items() if re.search(pattern, query, re.I)]
    return query + " " + " ".join(fields)
