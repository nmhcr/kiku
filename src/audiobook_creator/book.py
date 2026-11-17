"""Read a source text and split it into chapters for synthesis."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .config import DEFAULT_CHAPTER_PATTERN


@dataclass(frozen=True)
class Chapter:
    number: int
    title: str
    text: str


def read_source(path: str | Path) -> str:
    """Read a UTF-8 source text, with a helpful error for common mistakes."""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"source text not found: {source}")
    return source.read_text(encoding="utf-8")


def split_chapters(text: str, pattern: str = DEFAULT_CHAPTER_PATTERN) -> list[Chapter]:
    """Split ``text`` into chapters using a heading regex.

    Content before the first heading becomes a "Prelude" chapter. If no
    heading matches, the whole text becomes one "Full text" chapter.
    Chapters without any alphanumeric content are skipped.
    """
    regex = re.compile(pattern, re.IGNORECASE | re.MULTILINE)
    matches = list(regex.finditer(text))
    chapters: list[Chapter] = []

    if not matches:
        body = text.strip()
        if body:
            chapters.append(Chapter(1, "Full text", body))
        return chapters

    preamble = text[: matches[0].start()].strip()
    if preamble:
        chapters.append(Chapter(1, "Prelude", preamble))

    for index, match in enumerate(matches):
        title = (match.group(1) if match.groups() else match.group(0)).strip()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end() : end].strip()
        if not any(char.isalnum() for char in body):
            continue
        chapters.append(Chapter(len(chapters) + 1, title or f"Chapter {index + 1}", body))

    return chapters
