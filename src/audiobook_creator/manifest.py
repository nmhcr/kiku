"""Resumable progress tracking, stored as ``manifest.json`` in the project."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .book import Chapter

MANIFEST_FILENAME = "manifest.json"


@dataclass
class ChapterState:
    number: int
    title: str
    status: str = "pending"  # pending | done | failed
    file: str | None = None
    duration_seconds: float | None = None
    updated_at: str | None = None


@dataclass
class Manifest:
    title: str
    version: int = 1
    chapters: list[ChapterState] = field(default_factory=list)

    def sync(self, chapters: list[Chapter]) -> None:
        """Add new chapters and refresh titles, keeping existing status."""
        existing = {state.number: state for state in self.chapters}
        updated: list[ChapterState] = []
        for chapter in chapters:
            state = existing.get(chapter.number)
            if state is None:
                state = ChapterState(chapter.number, chapter.title)
            else:
                state.title = chapter.title
            updated.append(state)
        self.chapters = updated

    def is_done(self, number: int) -> bool:
        return any(c.number == number and c.status == "done" for c in self.chapters)

    def pending_numbers(self) -> list[int]:
        return [c.number for c in self.chapters if c.status != "done"]

    def mark_done(self, number: int, filename: str, duration: float) -> None:
        state = self._get(number)
        state.status = "done"
        state.file = filename
        state.duration_seconds = round(duration, 3)
        state.updated_at = _now()

    def mark_failed(self, number: int) -> None:
        state = self._get(number)
        state.status = "failed"
        state.updated_at = _now()

    def _get(self, number: int) -> ChapterState:
        for state in self.chapters:
            if state.number == number:
                return state
        raise KeyError(f"chapter {number} is not in the manifest")

    def completed_files(self, base: Path) -> list[Path]:
        files: list[Path] = []
        for state in sorted(self.chapters, key=lambda c: c.number):
            if state.status == "done" and state.file:
                path = Path(state.file)
                files.append(path if path.is_absolute() else base / path)
        return files

    @classmethod
    def load(cls, path: str | Path, title: str) -> "Manifest":
        manifest_path = Path(path)
        if not manifest_path.is_file():
            return cls(title=title)
        data: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
        states = [ChapterState(**item) for item in data.get("chapters", [])]
        return cls(title=data.get("title", title), version=data.get("version", 1), chapters=states)

    def save(self, path: str | Path) -> None:
        manifest_path = Path(path)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        temp = manifest_path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")
        os.replace(temp, manifest_path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
