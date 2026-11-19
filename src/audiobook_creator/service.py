"""Application service layer.

Plain, callable functions used by the CLI and, later, by a local web or desktop
frontend. No argparse, no printing: functions take paths and data, return
structured results, and report long-running work through progress callbacks.
"""

from __future__ import annotations

import math
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

import numpy as np

try:  # Windows has no fcntl; fall back to an in-process lock there.
    import fcntl
except ImportError:  # pragma: no cover - platform dependent
    fcntl = None

from .audio import assemble as assemble_audio
from .audio import split_groups, write_wav
from .book import Chapter, chapters_from_source, load_source
from .config import (
    CONFIG_FILENAME,
    Settings,
    apply_overrides,
    load_settings,
    render_settings,
)
from .manifest import MANIFEST_FILENAME, Manifest
from .synthesis import create_synthesizer


@dataclass
class ChapterInfo:
    number: int
    title: str
    status: str = "pending"
    duration_seconds: float | None = None
    file: str | None = None


@dataclass
class OutputFilePlan:
    part: int
    destination: Path
    first_chapter: int
    last_chapter: int
    chapter_count: int


@dataclass
class OutputFile:
    path: Path
    first_chapter: int
    last_chapter: int
    chapter_count: int
    size_bytes: int


@dataclass
class OutputPlan:
    total_chapters: int
    chapters_per_file: int
    files: list[OutputFilePlan]


@dataclass
class ProjectInfo:
    config_path: Path
    title: str
    author: str
    source: Path
    chapters: list[ChapterInfo]
    output_plan: OutputPlan


@dataclass
class SynthProgress:
    number: int
    title: str
    state: str  # "started" | "done" | "failed" | "paused" | "resumed" | "stopped"
    duration_seconds: float | None = None
    error: str | None = None


@dataclass
class SynthResult:
    synthesized: int
    failed: int
    skipped: int
    sample_rate: int = 0
    stopped: bool = False


class SynthesisInProgress(RuntimeError):
    """Raised when another job already holds the project's lock."""


class SynthesisControl:
    """Thread-safe pause/resume/stop signals for a synthesis job."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._condition = threading.Condition()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        with self._condition:
            self._paused.clear()
            self._condition.notify_all()

    def stop(self) -> None:
        self._stop.set()
        with self._condition:
            self._condition.notify_all()

    def wait_if_paused(self) -> None:
        with self._condition:
            while self._paused.is_set() and not self._stop.is_set():
                self._condition.wait(timeout=0.5)


_process_locks: dict[str, threading.Lock] = {}
_process_locks_guard = threading.Lock()


@contextmanager
def project_lock(config_path: Path, name: str) -> Iterator[None]:
    """Prevent two processes from running the same job on a project."""
    lock_path = config_path.parent / f".{name}.lock"
    if fcntl is not None:
        with open(lock_path, "a+") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise SynthesisInProgress(
                    f"another {name} job is already running for this project"
                ) from exc
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    else:  # pragma: no cover - platform dependent
        key = str(lock_path)
        with _process_locks_guard:
            lock = _process_locks.setdefault(key, threading.Lock())
        if not lock.acquire(blocking=False):
            raise SynthesisInProgress(
                f"another {name} job is already running for this project"
            )
        try:
            yield
        finally:
            lock.release()


@dataclass
class ProjectStatus:
    title: str
    author: str
    chapters: list[ChapterInfo]
    done: int
    failed: int
    total_seconds: float
    output_plan: OutputPlan


SynthCallback = Callable[[SynthProgress], None]


def _safe(value: object) -> str:
    return str(value).replace("/", "_").replace("\\", "_")


def destination_for(
    settings: Settings,
    label: str | int,
    chapter_numbers: tuple[int, int] | None,
) -> Path:
    """Build the destination path for one output part."""
    extension = ".mp3" if settings.audio.output_format == "mp3" else ".wav"
    pattern = settings.output_options.name_pattern
    if not pattern:
        if isinstance(label, str):
            return settings.output.with_suffix(extension)
        return settings.output.parent / f"{settings.output.stem}_part{label}{extension}"
    chapter = "all" if isinstance(label, str) else str(label)
    chapter_range = (
        "all"
        if chapter_numbers is None
        else f"{chapter_numbers[0]:03d}-{chapter_numbers[1]:03d}"
    )
    name = pattern.format(
        title=_safe(settings.title),
        chapter=_safe(chapter),
        range=_safe(chapter_range),
    )
    for old_extension in (".wav", ".mp3"):
        if name.lower().endswith(old_extension):
            name = name[: -len(old_extension)]
            break
    return settings.output.parent / (name + extension)


def build_output_plan(settings: Settings, chapter_numbers: list[int]) -> OutputPlan:
    """Describe how chapters would be grouped into audiobook files."""
    per_file = settings.output_options.chapters_per_file
    split_mode = per_file > 0
    plan = OutputPlan(total_chapters=len(chapter_numbers), chapters_per_file=per_file, files=[])
    if not chapter_numbers:
        return plan
    for part, group in enumerate(split_groups(chapter_numbers, per_file), start=1):
        numbers = (group[0], group[-1]) if split_mode else None
        label = part if split_mode else "all"
        plan.files.append(
            OutputFilePlan(
                part=part,
                destination=destination_for(settings, label, numbers),
                first_chapter=group[0],
                last_chapter=group[-1],
                chapter_count=len(group),
            )
        )
    return plan


def _config_path(config: str | Path) -> Path:
    return Path(config).expanduser().resolve()


def _load_manifest(config_path: Path, settings: Settings) -> Manifest:
    return Manifest.load(config_path.parent / MANIFEST_FILENAME, settings.title)


def _load_chapters(settings: Settings) -> list[Chapter]:
    return chapters_from_source(load_source(settings.source), settings.chapter_pattern)


def _chapter_infos(chapters: list[Chapter], manifest: Manifest) -> list[ChapterInfo]:
    states = {state.number: state for state in manifest.chapters}
    infos = []
    for chapter in chapters:
        state = states.get(chapter.number)
        infos.append(
            ChapterInfo(
                number=chapter.number,
                title=chapter.title,
                status=state.status if state else "pending",
                duration_seconds=state.duration_seconds if state else None,
                file=state.file if state else None,
            )
        )
    return infos


def create_project(
    source: str | Path,
    directory: str | Path,
    *,
    title: str | None = None,
    author: str | None = None,
    overrides: dict[str, Any] | None = None,
    overwrite: bool = False,
) -> ProjectInfo:
    """Create a project directory around a source text or EPUB."""
    source_path = Path(source).expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"source text not found: {source_path}")
    project = Path(directory).expanduser().resolve()
    project.mkdir(parents=True, exist_ok=True)
    (project / "audio").mkdir(exist_ok=True)
    (project / "out").mkdir(exist_ok=True)

    config_path = project / CONFIG_FILENAME
    if config_path.exists() and not overwrite:
        raise FileExistsError(f"{config_path} already exists")
    if source_path.name == "book.toml":
        raise ValueError("source file cannot be named book.toml")

    source_info = load_source(source_path)
    title = title or source_info.title
    author = author or source_info.author
    if not title:
        raise ValueError("a title is required (or EPUB metadata)")

    local_source = project / source_path.name
    if local_source.resolve() != source_path:
        local_source.write_bytes(source_path.read_bytes())

    # Anchor the weights directory absolutely when the model assets exist
    # relative to the caller; ONNX installs have no .pt file.
    checkpoint = Path("weights/tiny-voca.pt")
    weights_dir = checkpoint.parent
    has_onnx = (weights_dir / "config.json").is_file() and (
        weights_dir / "onnx" / "model.onnx"
    ).is_file()
    checkpoint_value = str(checkpoint.resolve()) if checkpoint.is_file() or has_onnx else str(checkpoint)

    settings = Settings(
        title=title,
        author=author,
        source=Path(local_source.name),
        checkpoint=Path(checkpoint_value),
        device="cpu",
        output=Path("out/audiobook.wav"),
        audio_dir=Path("audio"),
    )
    apply_overrides(settings, overrides or {})
    settings.validate()
    config_path.write_text(render_settings(settings), encoding="utf-8")
    Manifest(title=settings.title).save(config_path.parent / MANIFEST_FILENAME)

    chapters = chapters_from_source(source_info, settings.chapter_pattern)
    manifest = _load_manifest(config_path, settings)
    return ProjectInfo(
        config_path=config_path,
        title=settings.title,
        author=settings.author,
        source=local_source,
        chapters=_chapter_infos(chapters, manifest),
        output_plan=build_output_plan(settings, [chapter.number for chapter in chapters]),
    )


def get_settings(config: str | Path = CONFIG_FILENAME) -> Settings:
    return load_settings(config)[0]


def update_settings(
    config: str | Path, changes: dict[str, Any]
) -> tuple[Settings, bool]:
    """Persist changes to ``book.toml``; returns the settings and whether it changed."""
    config_path = _config_path(config)
    settings = load_settings(config_path)[0]
    changed = apply_overrides(settings, changes)
    settings.validate()
    if changed:
        config_path.write_text(render_settings(settings, config_path.parent), encoding="utf-8")
    return settings, changed


def list_chapters(config: str | Path = CONFIG_FILENAME) -> list[ChapterInfo]:
    config_path = _config_path(config)
    settings = get_settings(config_path)
    return _chapter_infos(_load_chapters(settings), _load_manifest(config_path, settings))


def get_status(config: str | Path = CONFIG_FILENAME) -> ProjectStatus:
    config_path = _config_path(config)
    settings = get_settings(config_path)
    chapters = _load_chapters(settings)
    manifest = _load_manifest(config_path, settings)
    manifest.sync(chapters)
    manifest.save(config_path.parent / MANIFEST_FILENAME)
    infos = _chapter_infos(chapters, manifest)
    return ProjectStatus(
        title=settings.title,
        author=settings.author,
        chapters=infos,
        done=sum(info.status == "done" for info in infos),
        failed=sum(info.status == "failed" for info in infos),
        total_seconds=sum(info.duration_seconds or 0 for info in infos),
        output_plan=build_output_plan(settings, [chapter.number for chapter in chapters]),
    )


def synthesize(
    config: str | Path = CONFIG_FILENAME,
    *,
    force: bool = False,
    only: list[int] | None = None,
    start: int | None = None,
    end: int | None = None,
    count: int | None = None,
    overrides: dict[str, Any] | None = None,
    progress: SynthCallback | None = None,
    control: SynthesisControl | None = None,
) -> SynthResult:
    """Synthesize selected chapters, updating the manifest as it goes.

    ``only``, ``start`` and ``end`` filter by chapter number; ``count`` limits
    the result to at most N chapters in book order. Pass ``control`` to pause
    or stop the job cooperatively between chapters.
    """
    config_path = _config_path(config)
    with project_lock(config_path, "synthesize"):
        settings = get_settings(config_path)
        apply_overrides(settings, overrides or {})
        settings.validate()
        chapters = _load_chapters(settings)
        if not chapters:
            raise ValueError("No chapters found in the source text.")

        manifest = _load_manifest(config_path, settings)
        manifest.sync(chapters)
        targets = [chapter for chapter in chapters if force or not manifest.is_done(chapter.number)]
        if only is not None:
            wanted = set(only)
            targets = [chapter for chapter in targets if chapter.number in wanted]
        if start is not None:
            targets = [chapter for chapter in targets if chapter.number >= start]
        if end is not None:
            targets = [chapter for chapter in targets if chapter.number <= end]
        if count is not None:
            if count < 0:
                raise ValueError("count cannot be negative")
            targets = targets[:count]

        skipped = len(chapters) - len(targets)
        result = SynthResult(synthesized=0, failed=0, skipped=skipped)
        if not targets:
            manifest.save(config_path.parent / MANIFEST_FILENAME)
            return result

        engine = create_synthesizer(settings).load()
        result.sample_rate = engine.sample_rate
        paused_reported = False
        for chapter in targets:
            if control and control.stopped:
                result.stopped = True
                if progress:
                    progress(SynthProgress(0, "", "stopped"))
                break
            if control and control.paused:
                if not paused_reported and progress:
                    progress(SynthProgress(chapter.number, chapter.title, "paused"))
                    paused_reported = True
                control.wait_if_paused()
                if control.stopped:
                    result.stopped = True
                    if progress:
                        progress(SynthProgress(0, "", "stopped"))
                    break
                if paused_reported and progress:
                    progress(SynthProgress(chapter.number, chapter.title, "resumed"))
                    paused_reported = False

            if progress:
                progress(SynthProgress(chapter.number, chapter.title, "started"))
            filename = f"chapter_{chapter.number:04d}.wav"
            target = settings.audio_dir / filename
            try:
                samples = engine.synthesize(chapter.text)
                write_wav(target, samples, engine.sample_rate)
                duration = len(samples) / engine.sample_rate
            except Exception as exc:
                manifest.mark_failed(chapter.number)
                result.failed += 1
                if progress:
                    progress(SynthProgress(chapter.number, chapter.title, "failed", error=str(exc)))
                continue
            try:
                stored = str(target.relative_to(config_path.parent))
            except ValueError:
                stored = str(target)
            manifest.mark_done(chapter.number, stored, duration)
            result.synthesized += 1
            if progress:
                progress(SynthProgress(chapter.number, chapter.title, "done", duration_seconds=duration))
        manifest.save(config_path.parent / MANIFEST_FILENAME)
        return result


def assemble(
    config: str | Path = CONFIG_FILENAME,
    *,
    overrides: dict[str, Any] | None = None,
    num_files: int | None = None,
    progress: Callable[[OutputFile], None] | None = None,
) -> list[OutputFile]:
    """Assemble completed chapters into one or more audiobook files.

    ``num_files`` is a convenience: it derives ``chapters_per_file`` from the
    number of completed chapters so the output is split into about N files.
    """
    config_path = _config_path(config)
    with project_lock(config_path, "assemble"):
        settings = get_settings(config_path)
        apply_overrides(settings, overrides or {})
        settings.validate()

        manifest = _load_manifest(config_path, settings)
        states = sorted(
            (state for state in manifest.chapters if state.status == "done" and state.file),
            key=lambda state: state.number,
        )
        files = manifest.completed_files(config_path.parent, settings.audio_dir)
        if not files:
            raise ValueError("No synthesized chapters found. Run 'synthesize' first.")

        if num_files is not None:
            if num_files <= 0:
                raise ValueError("num_files must be greater than zero")
            per_file = max(1, math.ceil(len(files) / num_files))
            settings.output_options.chapters_per_file = per_file
            settings.output_options.validate()

        entries = [(state.number, path) for state, path in zip(states, files)]
        plan = build_output_plan(settings, [number for number, _path in entries])
        written: list[OutputFile] = []
        groups = split_groups(entries, settings.output_options.chapters_per_file)
        for output_plan, group_entries in zip(plan.files, groups):
            group = [path for _number, path in group_entries]
            assembled = assemble_audio(group, output_plan.destination, settings.audio)
            result = OutputFile(
                path=assembled,
                first_chapter=output_plan.first_chapter,
                last_chapter=output_plan.last_chapter,
                chapter_count=output_plan.chapter_count,
                size_bytes=assembled.stat().st_size,
            )
            written.append(result)
            if progress:
                progress(result)
        return written


def preview_voice(
    config: str | Path,
    text: str,
    overrides: dict[str, Any] | None = None,
) -> tuple[np.ndarray, int]:
    """Synthesize one sentence for auditioning a voice without rendering a book."""
    settings = get_settings(config)
    apply_overrides(settings, overrides or {})
    settings.validate()
    engine = create_synthesizer(settings).load()
    return engine.synthesize(text), engine.sample_rate
