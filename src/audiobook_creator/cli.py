"""Command line interface: ``audiobook-creator`` or ``python -m audiobook_creator``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .audio import assemble, write_wav
from .book import read_source, split_chapters
from .config import CONFIG_FILENAME, render_config, load_settings
from .manifest import MANIFEST_FILENAME, Manifest
from .synthesis import EngineError, Synthesizer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="audiobook-creator",
        description="Create an audiobook from a text file using tiny-voca.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="create a new audiobook project directory")
    init.add_argument("--dir", type=Path, default=Path("."), help="project directory")
    init.add_argument("--source", type=Path, required=True, help="source text file")
    init.add_argument("--title", required=True, help="book title")
    init.add_argument("--author", default="", help="book author")
    init.add_argument("--voice", default="F2", choices=("F2", "M2"))
    init.add_argument("--checkpoint", default="weights/tiny-voca.pt")
    init.add_argument("--force", action="store_true", help="overwrite existing config")

    syn = sub.add_parser("synthesize", help="synthesize pending chapters")
    syn.add_argument("--config", default=CONFIG_FILENAME, help="path to book.toml")
    syn.add_argument("--force", action="store_true", help="regenerate all chapters")
    syn.add_argument("--only", type=int, nargs="+", help="synthesize specific chapter numbers")

    ass = sub.add_parser("assemble", help="join chapter clips into one audiobook")
    ass.add_argument("--config", default=CONFIG_FILENAME)
    ass.add_argument("--output", type=Path, help="override output path")
    ass.add_argument("--format", choices=("wav", "mp3"), help="override output format")

    status = sub.add_parser("status", help="show synthesis progress")
    status.add_argument("--config", default=CONFIG_FILENAME)

    sub.add_parser("voices", help="list available voices")
    return parser


def _project_root(config_path: Path) -> Path:
    return config_path.parent


def _manifest_path(config_path: Path) -> Path:
    return _project_root(config_path) / MANIFEST_FILENAME


def command_init(args: argparse.Namespace) -> int:
    project = args.dir.expanduser().resolve()
    project.mkdir(parents=True, exist_ok=True)
    (project / "audio").mkdir(exist_ok=True)
    (project / "out").mkdir(exist_ok=True)

    config_path = project / CONFIG_FILENAME
    if config_path.exists() and not args.force:
        print(f"{config_path} already exists (use --force to overwrite).", file=sys.stderr)
        return 1

    source = args.source.expanduser().resolve()
    if not source.is_file():
        print(f"source text not found: {source}", file=sys.stderr)
        return 1

    # Copy the source into the project so it is self-contained.
    local_source = project / source.name
    if local_source.resolve() != source:
        local_source.write_bytes(source.read_bytes())

    # If the checkpoint already exists relative to the current directory,
    # store an absolute path so weights can live outside the project dir.
    checkpoint = Path(args.checkpoint).expanduser()
    checkpoint_value = str(checkpoint.resolve()) if checkpoint.is_file() else args.checkpoint

    config_path.write_text(
        render_config(
            title=args.title,
            author=args.author,
            source=local_source.name,
            checkpoint=checkpoint_value,
            voice=args.voice,
        ),
        encoding="utf-8",
    )
    Manifest(title=args.title).save(_manifest_path(config_path))
    print(f"Created project at {project}")
    print(f"Next: set up the engine (scripts/setup.sh), then:")
    print(f"  audiobook-creator synthesize --config {config_path}")
    return 0


def command_synthesize(args: argparse.Namespace) -> int:
    settings, config_path = load_settings(args.config)
    chapters = split_chapters(read_source(settings.source), settings.chapter_pattern)
    if not chapters:
        print("No chapters found in the source text.", file=sys.stderr)
        return 1

    manifest = Manifest.load(_manifest_path(config_path), settings.title)
    manifest.sync(chapters)

    targets = [c for c in chapters if args.force or not manifest.is_done(c.number)]
    if args.only:
        wanted = set(args.only)
        targets = [c for c in targets if c.number in wanted]
    if not targets:
        print("All chapters are already synthesized (use --force to regenerate).")
        manifest.save(_manifest_path(config_path))
        return 0

    try:
        engine = Synthesizer(settings.checkpoint, settings.voice, settings.device).load()
    except EngineError as exc:
        print(f"Engine error: {exc}", file=sys.stderr)
        return 1

    print(f"Loaded tiny-voca ({engine.sample_rate} Hz), synthesizing {len(targets)} chapter(s).")
    failed = 0
    for chapter in targets:
        filename = f"chapter_{chapter.number:04d}.wav"
        target = settings.audio_dir / filename
        try:
            samples = engine.synthesize(chapter.text)
            write_wav(target, samples, engine.sample_rate)
            duration = len(samples) / engine.sample_rate
        except Exception as exc:  # keep going so one bad chapter doesn't lose the rest
            print(f"  [{chapter.number:>3}] FAILED {chapter.title!r}: {exc}", file=sys.stderr)
            manifest.mark_failed(chapter.number)
            failed += 1
            continue
        manifest.mark_done(chapter.number, filename, duration)
        print(f"  [{chapter.number:>3}] {chapter.title!r} -> {target.name} ({duration:.1f}s)")

    manifest.save(_manifest_path(config_path))
    print(f"Done. {len(targets) - failed} synthesized, {failed} failed.")
    print(f"Assemble with: audiobook-creator assemble --config {config_path}")
    return 1 if failed else 0


def command_assemble(args: argparse.Namespace) -> int:
    settings, config_path = load_settings(args.config)
    if args.output:
        settings.output = args.output.expanduser().resolve()
    if args.format:
        settings.audio.output_format = args.format
        if args.format == "mp3" and settings.output.suffix.lower() != ".mp3":
            settings.output = settings.output.with_suffix(".mp3")

    manifest = Manifest.load(_manifest_path(config_path), settings.title)
    files = manifest.completed_files(_project_root(config_path))
    if not files:
        print("No synthesized chapters found. Run 'synthesize' first.", file=sys.stderr)
        return 1
    try:
        result = assemble(files, settings.output, settings.audio)
    except (ValueError, RuntimeError) as exc:
        print(f"Assembly failed: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote {result} ({len(files)} chapters)")
    return 0


def command_status(args: argparse.Namespace) -> int:
    settings, config_path = load_settings(args.config)
    chapters = split_chapters(read_source(settings.source), settings.chapter_pattern)
    manifest = Manifest.load(_manifest_path(config_path), settings.title)
    manifest.sync(chapters)
    manifest.save(_manifest_path(config_path))

    done = sum(1 for c in manifest.chapters if c.status == "done")
    total_seconds = sum(c.duration_seconds or 0 for c in manifest.chapters)
    print(f"{settings.title}" + (f" — {settings.author}" if settings.author else ""))
    print(f"Chapters: {done}/{len(manifest.chapters)} synthesized ({total_seconds / 60:.1f} min)")
    for state in manifest.chapters:
        mark = {"done": "✓", "failed": "✗", "pending": "·"}.get(state.status, "?")
        print(f"  {mark} {state.number:>3}. {state.title}")
    return 0


def command_voices(_args: argparse.Namespace) -> int:
    print("tiny-voca voices (American English, 24 kHz):")
    print("  F2  female")
    print("  M2  male")
    print("Note: English only, two fixed voices, no voice cloning.")
    return 0


COMMANDS = {
    "init": command_init,
    "synthesize": command_synthesize,
    "assemble": command_assemble,
    "status": command_status,
    "voices": command_voices,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
