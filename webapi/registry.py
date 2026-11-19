"""Maps short project IDs to ``book.toml`` paths on local disk."""

from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ProjectNotFound(KeyError):
    """Raised when an ID is unknown or its config file has disappeared."""


class Registry:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "registry.json"
        self._lock = threading.Lock()
        self._projects: dict[str, dict] = self._load()

    def _load(self) -> dict[str, dict]:
        if not self.index_path.is_file():
            return {}
        data = json.loads(self.index_path.read_text(encoding="utf-8"))
        return data.get("projects", {}) if isinstance(data, dict) else {}

    def _save(self) -> None:
        temp = self.index_path.with_suffix(".json.tmp")
        temp.write_text(
            json.dumps({"projects": self._projects}, indent=2) + "\n", encoding="utf-8"
        )
        temp.replace(self.index_path)

    @staticmethod
    def new_id(title: str | None, filename: str | None = None) -> str:
        base = re.sub(r"[^a-z0-9]+", "-", (title or filename or "book").lower()).strip("-")
        return f"{base[:40] or 'book'}-{uuid.uuid4().hex[:6]}"

    @staticmethod
    def check_id(project_id: str) -> None:
        if not _ID_RE.fullmatch(project_id):
            raise ValueError("invalid project id")

    def add(self, project_id: str, config_path: str | Path) -> dict:
        self.check_id(project_id)
        entry = {
            "config_path": str(Path(config_path).resolve()),
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        with self._lock:
            if project_id in self._projects:
                raise ValueError(f"project already exists: {project_id}")
            self._projects[project_id] = entry
            self._save()
        return {"id": project_id, **entry}

    def get(self, project_id: str) -> Path:
        self.check_id(project_id)
        with self._lock:
            entry = self._projects.get(project_id)
        if entry is None:
            raise ProjectNotFound(project_id)
        config_path = Path(entry["config_path"])
        if not config_path.is_file():
            raise ProjectNotFound(project_id)
        return config_path

    def list(self) -> list[dict]:
        with self._lock:
            return [{"id": key, **value} for key, value in sorted(self._projects.items())]
