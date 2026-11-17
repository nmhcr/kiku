"""Extract readable text and metadata from an EPUB book.

EPUB is a ZIP of XHTML documents plus an OPF manifest. Everything needed is
in the standard library: no extra dependency for EPUB input. Documents are
read in spine (reading) order, and headings are kept on their own lines so
the normal chapter-heading regex keeps working.
"""

from __future__ import annotations

import posixpath
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

from .book import BookSource

_BLOCK_TAGS = frozenset(
    ("p", "div", "section", "article", "blockquote", "br", "hr", "li", "ul",
     "ol", "tr", "table", "pre", "h1", "h2", "h3", "h4", "h5", "h6")
)
_SKIP_TAGS = frozenset(("script", "style", "head", "nav", "svg"))
_HEADING_TAGS = frozenset(("h1", "h2", "h3"))
_TEXT_TYPES = frozenset(
    ("application/xhtml+xml", "text/html", "application/xml", "text/xml")
)


class _Extractor(HTMLParser):
    """Turn XHTML into text, keeping block boundaries and headings."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.headings: list[str] = []
        self._skip_depth = 0
        self._heading_tag: str | None = None
        self._heading_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")
        if tag in _HEADING_TAGS:
            self._heading_tag = tag
            self._heading_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")
        if self._heading_tag == tag:
            heading = _clean("".join(self._heading_parts))
            if heading:
                self.headings.append(heading)
            self._heading_tag = None
            self._heading_parts = []

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        self.parts.append(data)
        if self._heading_tag:
            self._heading_parts.append(data)


def _clean(text: str) -> str:
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _safe_join(directory: str, name: str) -> str:
    """Join a zip-internal path without allowing ``..`` to escape the root."""
    joined = posixpath.normpath(posixpath.join(directory, name))
    if joined.startswith("../") or joined == "..":
        raise ValueError(f"invalid path in EPUB: {name!r}")
    return joined


def _decode(data: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _local(tag: str) -> str:
    """Return an XML tag's local name, ignoring its namespace."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _find_opf(archive: zipfile.ZipFile) -> str:
    try:
        container = ElementTree.fromstring(archive.read("META-INF/container.xml"))
    except KeyError as exc:
        raise ValueError("EPUB is missing META-INF/container.xml") from exc
    for element in container.iter():
        if _local(element.tag) == "rootfile" and element.get("full-path"):
            return _safe_join("", element.get("full-path", ""))
    raise ValueError("EPUB container.xml has no rootfile")


def read_epub(path: str | Path) -> BookSource:
    """Read an EPUB into a :class:`BookSource`.

    Raises ``ValueError`` for files that are not valid EPUBs, and
    ``FileNotFoundError`` for a missing file.
    """
    epub_path = Path(path)
    if not epub_path.is_file():
        raise FileNotFoundError(f"source text not found: {epub_path}")
    try:
        archive = zipfile.ZipFile(epub_path)
    except zipfile.BadZipFile as exc:
        raise ValueError(f"not a valid EPUB (ZIP) file: {epub_path}") from exc

    with archive:
        opf_path = _find_opf(archive)
        try:
            opf = ElementTree.fromstring(archive.read(opf_path))
        except KeyError as exc:
            raise ValueError(f"EPUB is missing its OPF file: {opf_path}") from exc

        metadata = opf.find("{*}metadata")
        title = ""
        author = ""
        if metadata is not None:
            for element in metadata.iter():
                if not element.text or not element.text.strip():
                    continue
                if _local(element.tag) == "title":
                    title = " ".join(part for part in (title, element.text.strip()) if part)
                elif _local(element.tag) == "creator":
                    author = ", ".join(part for part in (author, element.text.strip()) if part)

        manifest: dict[str, tuple[str, str]] = {}
        for item in opf.iter():
            if _local(item.tag) != "item":
                continue
            item_id = item.get("id")
            href = item.get("href")
            if item_id and href:
                manifest[item_id] = (_safe_join(posixpath.dirname(opf_path), href), item.get("media-type", ""))

        documents: list[tuple[str, str]] = []
        spine = next((element for element in opf.iter() if _local(element.tag) == "spine"), None)
        if spine is not None:
            for reference in spine.iter():
                if _local(reference.tag) != "itemref":
                    continue
                if reference.get("linear") == "no":
                    continue
                entry = manifest.get(reference.get("idref", ""))
                if entry is None:
                    continue
                name, media_type = entry
                if media_type and not media_type.lower().startswith(("application/xhtml", "text/html")):
                    continue
                if not name.lower().endswith((".xhtml", ".html", ".htm")) and media_type not in _TEXT_TYPES:
                    continue
                try:
                    data = archive.read(name)
                except KeyError:
                    continue
                extractor = _Extractor()
                extractor.feed(_decode(data))
                text = _clean("".join(extractor.parts))
                if not any(character.isalnum() for character in text):
                    continue
                heading = extractor.headings[0] if extractor.headings else Path(name).stem
                documents.append((heading, text))

    if not documents:
        raise ValueError(f"EPUB has no readable text documents: {epub_path}")
    text = "\n\n".join(document_text for _title, document_text in documents)
    return BookSource(
        text=text,
        title=_clean(title),
        author=_clean(author),
        documents=documents,
    )
