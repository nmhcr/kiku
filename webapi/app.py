"""FastAPI application exposing the service layer on localhost."""

from __future__ import annotations

import asyncio
import json
import os
import queue
import re
import threading
import uuid
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from starlette.datastructures import UploadFile

from audiobook_creator import service
from audiobook_creator.audio import write_wav
from audiobook_creator.config import parse_chapters_per_file

from .registry import ProjectNotFound, Registry

_OPTIONAL_NUMBERS = {"speed", "pitch_shift", "pitch_range"}
_OPTIONAL_INTS = {"seed", "chapter_gap_ms", "sample_rate"}
_ALLOWED_SUFFIXES = {".txt", ".md", ".epub"}


def _to_json(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: _to_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_json(item) for item in value]
    return value


def _response(value: Any) -> JSONResponse:
    return JSONResponse(content=_to_json(value))


def _config(request: Request, project_id: str) -> Path:
    registry: Registry = request.app.state.registry
    try:
        return registry.get(project_id)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail=f"project not found: {project_id}") from None
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid project id") from None


def _normalize_changes(changes: dict[str, Any]) -> dict[str, Any]:
    changes = dict(changes)
    if "chapters_per_file" in changes and changes["chapters_per_file"] is not None:
        try:
            changes["chapters_per_file"] = parse_chapters_per_file(changes["chapters_per_file"])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return changes


@dataclass
class Job:
    id: str
    project_id: str
    state: str = "queued"  # queued | running | done | failed
    events: list[dict[str, Any]] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None
    queue: queue.Queue | None = None


def create_app(projects_dir: str | Path | None = None) -> FastAPI:
    root = Path(
        projects_dir or os.environ.get("AUDIOBOOK_PROJECTS_DIR", Path.cwd() / "projects")
    ).expanduser()
    app = FastAPI(title="Audiobook Creator API", version="1.0")
    app.state.registry = Registry(root)
    app.state.jobs: dict[str, Job] = {}
    app.state.tasks: dict[str, asyncio.Task] = {}

    static_dir = Path(__file__).parent / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/")
    def index():
        return {
            "name": "Audiobook Creator API",
            "docs": "/docs",
            "projects": "/api/projects",
            "static_ui": "/static/index.html" if static_dir.is_dir() else None,
        }

    @app.post("/api/projects")
    async def create_project(request: Request):
        form = await request.form()
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise HTTPException(status_code=400, detail="a 'file' upload is required")
        raw_name = Path(upload.filename or "book.txt").name
        filename = re.sub(r"[^A-Za-z0-9._ -]", "_", raw_name)
        if Path(filename).suffix.lower() not in _ALLOWED_SUFFIXES:
            raise HTTPException(
                status_code=400,
                detail="supported source files: .txt, .md, .epub",
            )
        title = str(form.get("title") or "") or None
        project_id = Registry.new_id(title, filename)
        project_dir = app.state.registry.root / project_id
        project_dir.mkdir(parents=True, exist_ok=True)
        source_path = project_dir / filename
        source_path.write_bytes(await upload.read())
        await upload.close()

        overrides: dict[str, Any] = {}
        for key in (
            "voice", "speed", "pitch_shift", "pitch_range", "seed", "backend",
            "device", "chapter_gap_ms", "sample_rate", "name_pattern", "chapters_per_file",
        ):
            raw = form.get(key)
            if raw in (None, ""):
                continue
            value: Any = str(raw)
            if key in _OPTIONAL_NUMBERS:
                try:
                    value = float(value)
                except ValueError:
                    raise HTTPException(status_code=400, detail=f"{key} must be a number") from None
            elif key in _OPTIONAL_INTS:
                try:
                    value = int(value)
                except ValueError:
                    raise HTTPException(status_code=400, detail=f"{key} must be an integer") from None
            elif key == "chapters_per_file":
                try:
                    value = parse_chapters_per_file(value)
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
            overrides[key] = value

        try:
            info = await asyncio.to_thread(
                service.create_project,
                source_path,
                project_dir,
                title=title,
                author=str(form.get("author") or "") or None,
                overrides=overrides,
                overwrite=True,
            )
            app.state.registry.add(project_id, info.config_path)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _response({"id": project_id, **asdict(info)})

    @app.get("/api/projects")
    def list_projects():
        return _response({"projects": app.state.registry.list()})

    @app.get("/api/projects/{project_id}")
    def status(project_id: str, request: Request):
        return _response(service.get_status(_config(request, project_id)))

    @app.patch("/api/projects/{project_id}")
    async def update(project_id: str, request: Request, changes: dict[str, Any]):
        config = _config(request, project_id)
        try:
            settings, changed = await asyncio.to_thread(
                service.update_settings, config, _normalize_changes(changes)
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _response({"settings": settings, "changed": changed})

    @app.post("/api/projects/{project_id}/synthesize")
    async def synthesize(project_id: str, request: Request, options: dict[str, Any] | None = None):
        config = _config(request, project_id)
        options = dict(options or {})
        job_id = uuid.uuid4().hex[:12]
        event_queue: queue.Queue = queue.Queue()
        job = Job(id=job_id, project_id=project_id, queue=event_queue)
        app.state.jobs[job_id] = job

        def on_progress(event: Any) -> None:
            payload = _to_json(event)
            job.events.append(payload)
            event_queue.put(payload)

        def run() -> None:
            # A real thread (not an asyncio task) so the job survives request
            # boundaries in both uvicorn and the test client.
            job.state = "running"
            try:
                result = service.synthesize(
                    config,
                    force=bool(options.get("force", False)),
                    only=options.get("only"),
                    overrides=_normalize_changes(options.get("overrides") or {}),
                    progress=on_progress,
                )
                job.result = _to_json(result)
                job.state = "done"
                event_queue.put({"type": "done", "result": job.result})
            except Exception as exc:
                job.error = str(exc)
                job.state = "failed"
                event_queue.put({"type": "error", "message": str(exc)})
            finally:
                event_queue.put(None)

        app.state.tasks[job_id] = threading.Thread(target=run, daemon=True)
        app.state.tasks[job_id].start()
        return _response({"job_id": job_id})

    @app.get("/api/projects/{project_id}/jobs/{job_id}")
    def get_job(project_id: str, job_id: str, request: Request):
        job = app.state.jobs.get(job_id)
        if job is None or job.project_id != project_id:
            raise HTTPException(status_code=404, detail="job not found")
        return _response(
            {
                "id": job.id,
                "project_id": job.project_id,
                "state": job.state,
                "events": job.events,
                "result": job.result,
                "error": job.error,
            }
        )

    @app.get("/api/projects/{project_id}/events")
    async def events(project_id: str, job_id: str, request: Request):
        job = app.state.jobs.get(job_id)
        if job is None or job.project_id != project_id or job.queue is None:
            raise HTTPException(status_code=404, detail="job not found")
        event_queue = job.queue

        async def stream():
            while True:
                try:
                    item = await asyncio.to_thread(event_queue.get, True, 0.1)
                except queue.Empty:
                    continue
                if item is None:
                    break
                yield f"data: {json.dumps(item)}\n\n"

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/projects/{project_id}/assemble")
    async def assemble(project_id: str, request: Request, options: dict[str, Any] | None = None):
        config = _config(request, project_id)
        options = dict(options or {})
        try:
            files = await asyncio.to_thread(
                service.assemble,
                config,
                overrides=_normalize_changes(options.get("overrides") or {}),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (RuntimeError, OSError) as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return _response(files)

    @app.get("/api/projects/{project_id}/audio/{filename:path}")
    def audio(project_id: str, filename: str, request: Request):
        root = _config(request, project_id).parent.resolve()
        target = (root / filename).resolve()
        if not target.is_file():
            # Outputs normally live under out/; allow lookup by basename.
            matches = [path for path in root.rglob(Path(filename).name) if path.is_file()]
            if len(matches) == 1:
                target = matches[0].resolve()
        if not target.is_relative_to(root):
            raise HTTPException(status_code=400, detail="invalid audio path")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="audio file not found")
        media_type = "audio/mpeg" if target.suffix.lower() == ".mp3" else "audio/wav"
        return FileResponse(target, media_type=media_type)

    @app.post("/api/projects/{project_id}/preview")
    def preview(project_id: str, request: Request, payload: dict[str, Any]):
        config = _config(request, project_id)
        text = str(payload.get("text") or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="text is required")
        try:
            wave, rate = service.preview_voice(config, text)
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        path = config.parent / f"preview-{uuid.uuid4().hex[:8]}.wav"
        write_wav(path, wave, rate)
        return FileResponse(
            path,
            media_type="audio/wav",
            background=BackgroundTask(path.unlink, missing_ok=True),
        )

    return app


def main() -> None:
    import uvicorn

    uvicorn.run(
        create_app(),
        host=os.environ.get("AUDIOBOOK_HOST", "127.0.0.1"),
        port=int(os.environ.get("AUDIOBOOK_PORT", "8000")),
    )


if __name__ == "__main__":
    main()
