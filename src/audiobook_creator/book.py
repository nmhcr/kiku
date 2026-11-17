"""Read a source text and split it into chapters for synthesis."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import DEFAULT_CHAPTER_PATTERN


@dataclass
class BookSource:
    """Readable text plus optional metadata and document boundaries."""

    text: str
    title: str = ""
    author: str = ""
    documents: list[tuple[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class Chapter:
    number: int
    title: str
    text: str


def load_source(path: str | Path) -> BookSource:
    """Read a source file, dispatching on its extension.

    ``.epub`` is handled by the standard-library EPUB reader; everything else
    is read as UTF-8 plain text.
    """
    source_path = Path(path)
    if source_path.suffix.lower() == ".epub":
        from .epub import read_epub

        return read_epub(source_path)
    if not source_path.is_file():
        raise FileNotFoundError(f"source text not found: {source_path}")
    return BookSource(text=source_path.read_text(encoding="utf-8"))


def read_source(path: str | Path) -> str:
    """Read a source file and return just its text."""
    return load_source(path).text


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


def chapters_from_source(
    source: BookSource, pattern: str = DEFAULT_CHAPTER_PATTERN
) -> list[Chapter]:
    """Split a source into chapters.

    Structured sources (EPUB) can provide document boundaries. They are used
    only when the text contains no explicit chapter headings, so a book with
    proper "Chapter One" headings still splits exactly as before.
    """
    chapters = split_chapters(source.text, pattern)
    if len(chapters) == 1 and chapters[0].title == "Full text" and len(source.documents) > 1:
        chapters = []
        for title, body in source.documents:
            if not any(character.isalnum() for character in body):
                continue
            number = len(chapters) + 1
            chapters.append(Chapter(number, title or f"Part {number}", body))
    return chapters
