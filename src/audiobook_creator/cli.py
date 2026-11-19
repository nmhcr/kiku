"""Command line interface: ``kiku`` or ``python -m audiobook_creator``.

The CLI is a thin presentation layer: it parses flags and formats output, while
all application logic lives in :mod:`audiobook_creator.service`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .config import CONFIG_FILENAME, parse_chapters_per_file
from .service import (
    OutputPlan,
    SynthProgress,
    assemble as assemble_service,
    build_output_plan,
    create_project,
    destination_for as _destination_for,  # kept as an alias for existing tests
    get_settings,
    get_status,
    list_chapters,
    synthesize as synthesize_service,
    update_settings,
)


def _add_voice_flags(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("voice (overrides book.toml)")
    group.add_argument("--voice", choices=("F2", "M2"), help="F2 (female) or M2 (male)")
    group.add_argument("--speed", type=float, help="speaking rate, e.g. 1.1")
    group.add_argument("--pitch-shift", type=float, help="added to log F0 (~0.058 per semitone)")
    group.add_argument("--pitch-range", type=float, help="pitch movement scale, e.g. 1.2")
    group.add_argument("--seed", type=int, help="prosody seed")


def _add_engine_flags(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("engine (overrides book.toml)")
    group.add_argument("--backend", choices=("onnx", "torch"), help="inference backend (default: onnx)")
    group.add_argument("--device", help="torch device, e.g. cpu")
    group.add_argument("--checkpoint", help="path to tiny-voca checkpoint")


def _add_audio_flags(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("audio (overrides book.toml)")
    group.add_argument("--chapter-gap-ms", type=int, help="silence between chapters in ms")
    group.add_argument("--sample-rate", type=int, help="expected sample rate")
    group.add_argument("--format", dest="output_format", choices=("wav", "mp3"), help="output format")
    group.add_argument("--bitrate", dest="mp3_bitrate", help="mp3 bitrate, e.g. 192k")


def _add_output_flags(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("output files (overrides book.toml)")
    group.add_argument(
        "--chapters-per-file",
        type=parse_chapters_per_file,
        metavar="N|all",
        help="chapters per output file; 0 or 'all' means one file for the whole book",
    )
    group.add_argument(
        "--name-pattern",
        help="filename pattern: {title}, {chapter} (part number or 'all'), {range}",
    )
    group.add_argument("--output", type=Path, help="output file path (or output directory in split mode)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kiku",
        description="Create an audiobook from a text file using tiny-voca.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="create a new audiobook project directory")
    init.add_argument("--dir", type=Path, default=Path("."), help="project directory")
    init.add_argument("--source", type=Path, required=True, help="source text file")
    init.add_argument("--title", help="book title (default: EPUB metadata title)")
    init.add_argument("--author", default="", help="book author")
    init.add_argument("--force", action="store_true", help="overwrite existing config")
    _add_voice_flags(init)
    _add_engine_flags(init)
    _add_audio_flags(init)
    _add_output_flags(init)
    init.set_defaults(voice="F2")

    cfg = sub.add_parser("config", help="show or persist configuration settings")
    cfg.add_argument("--config", default=CONFIG_FILENAME, help="path to book.toml")
    cfg.add_argument("--author", help="book author")
    cfg.add_argument("--title", help="book title (also used by {title} in name patterns)")
    cfg.add_argument("--chapter-pattern", help="regex for chapter headings")
    _add_voice_flags(cfg)
    _add_engine_flags(cfg)
    _add_audio_flags(cfg)
    _add_output_flags(cfg)

    syn = sub.add_parser("synthesize", help="synthesize pending chapters")
    syn.add_argument("--config", default=CONFIG_FILENAME, help="path to book.toml")
    syn.add_argument("--force", action="store_true", help="regenerate all chapters")
    syn.add_argument("--only", type=int, nargs="+", help="synthesize specific chapter numbers")
    _add_voice_flags(syn)
    _add_engine_flags(syn)

    ass = sub.add_parser("assemble", help="join chapter clips into one or more audiobooks")
    ass.add_argument("--config", default=CONFIG_FILENAME)
    ass.add_argument("--title", help="book title (also used by {title} in name patterns)")
    _add_audio_flags(ass)
    _add_output_flags(ass)

    status = sub.add_parser("status", help="show synthesis progress and output plan")
    status.add_argument("--config", default=CONFIG_FILENAME)

    sub.add_parser("voices", help="list available voices")
    return parser


SETTING_ATTRS = (
    "title",
    "author",
    "chapter_pattern",
    "voice",
    "speed",
    "pitch_shift",
    "pitch_range",
    "seed",
    "backend",
    "device",
    "checkpoint",
    "chapter_gap_ms",
    "sample_rate",
    "output_format",
    "mp3_bitrate",
    "chapters_per_file",
    "name_pattern",
    "output",
)


def _overrides_from_args(args: argparse.Namespace) -> dict:
    return {
        name: getattr(args, name)
        for name in SETTING_ATTRS
        if getattr(args, name, None) is not None
    }


def _describe_plan(plan: OutputPlan) -> str:
    if plan.chapters_per_file <= 0:
        return f"one file with all {plan.total_chapters} chapter(s)"
    sizes = ", ".join(str(file.chapter_count) for file in plan.files)
    return f"{len(plan.files)} file(s) with {sizes} chapter(s)"


def command_init(args: argparse.Namespace) -> int:
    overrides = _overrides_from_args(args)
    if not args.author:
        overrides.pop("author", None)  # allow EPUB metadata to supply it
    try:
        project = create_project(
            args.source,
            args.dir,
            title=args.title,
            author=args.author or None,
            overrides=overrides,
            overwrite=args.force,
        )
    except FileExistsError as exc:
        print(f"{exc} (use --force to overwrite).", file=sys.stderr)
        return 1
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Created project at {project.config_path.parent}")
    print(f"Output plan: {_describe_plan(project.output_plan)}")
    print("Next: set up the engine (scripts/setup.sh), then:")
    print(f"  uv run kiku synthesize --config {project.config_path}")
    return 0


def command_config(args: argparse.Namespace) -> int:
    config_path = Path(args.config).expanduser().resolve()
    settings, changed = update_settings(args.config, _overrides_from_args(args))
    if changed:
        print(f"Updated {config_path}")
    try:
        chapter_count = len(list_chapters(args.config))
    except FileNotFoundError:
        chapter_count = 0
    print(f"Title: {settings.title}  Author: {settings.author or '-'}")
    print(f"Engine: {settings.backend}  device: {settings.device}")
    print(
        f"Voice: {settings.voice.voice}  speed={settings.voice.speed}  "
        f"pitch_shift={settings.voice.pitch_shift}  pitch_range={settings.voice.pitch_range}  "
        f"seed={settings.voice.seed}"
    )
    print(
        f"Audio: {settings.audio.output_format}  gap={settings.audio.chapter_gap_ms}ms  "
        f"sample_rate={settings.audio.sample_rate}  bitrate={settings.audio.mp3_bitrate}"
    )
    print(
        f"Splitting: {settings.output_options.chapters_per_file or 'all'} chapters/file; "
        f"name_pattern={settings.output_options.name_pattern or '(default)'}"
    )
    plan = build_output_plan(settings, list(range(1, chapter_count + 1)))
    print(f"Chapters found: {chapter_count}; output plan: {_describe_plan(plan)}")
    if plan.files:
        print(f"Example first output: {plan.files[0].destination}")
    return 0


def command_synthesize(args: argparse.Namespace) -> int:
    def on_progress(event: SynthProgress) -> None:
        if event.state == "failed":
            print(f"  [{event.number:>3}] FAILED {event.title!r}: {event.error}", file=sys.stderr)
        elif event.state == "done":
            duration = event.duration_seconds or 0.0
            print(f"  [{event.number:>3}] {event.title!r} -> chapter_{event.number:04d}.wav ({duration:.1f}s)")

    try:
        result = synthesize_service(
            args.config,
            force=args.force,
            only=args.only,
            overrides=_overrides_from_args(args),
            progress=on_progress,
        )
    except RuntimeError as exc:
        print(f"Engine error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    attempted = result.synthesized + result.failed
    if not attempted:
        print("All chapters are already synthesized (use --force to regenerate).")
        return 0
    print(f"Loaded tiny-voca ({result.sample_rate} Hz)")
    print(f"Done. {result.synthesized} synthesized, {result.failed} failed.")
    print(f"Assemble with: uv run kiku assemble --config {args.config}")
    return 1 if result.failed else 0


def command_assemble(args: argparse.Namespace) -> int:
    try:
        written = assemble_service(args.config, overrides=_overrides_from_args(args))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (RuntimeError, OSError) as exc:
        print(f"Assembly failed: {exc}", file=sys.stderr)
        return 1

    total_chapters = len(list_chapters(args.config))
    for file in written:
        if file.chapter_count == total_chapters:
            note = f"all {file.chapter_count} chapters"
        elif file.first_chapter == file.last_chapter:
            note = f"chapter {file.first_chapter}"
        else:
            note = f"chapters {file.first_chapter}-{file.last_chapter}"
        print(f"  {file.path.name}: {note} ({file.chapter_count} clip(s))")
    print(f"Wrote {len(written)} audiobook file(s).")
    return 0


def command_status(args: argparse.Namespace) -> int:
    status = get_status(args.config)
    print(f"{status.title}" + (f" — {status.author}" if status.author else ""))
    print(
        f"Chapters: {status.done}/{len(status.chapters)} synthesized "
        f"({status.total_seconds / 60:.1f} min)"
    )
    print(f"Output plan: {_describe_plan(status.output_plan)}")
    marks = {"done": "✓", "failed": "✗", "pending": "·"}
    for info in status.chapters:
        print(f"  {marks.get(info.status, '?')} {info.number:>3}. {info.title}")
    return 0


def command_voices(_args: argparse.Namespace) -> int:
    print("tiny-voca voices (American English, 24 kHz):")
    print("  F2  female")
    print("  M2  male")
    print("Note: English only, two fixed voices, no voice cloning.")
    return 0


COMMANDS = {
    "init": command_init,
    "config": command_config,
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
