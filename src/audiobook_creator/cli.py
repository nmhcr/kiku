"""Command line interface: ``audiobook-creator`` or ``python -m audiobook_creator``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .audio import assemble, split_groups, write_wav
from .book import chapters_from_source, load_source
from .config import (
    AudioSettings,
    CONFIG_FILENAME,
    OutputSettings,
    Settings,
    VoiceSettings,
    load_settings,
    parse_chapters_per_file,
    render_settings,
)
from .manifest import MANIFEST_FILENAME, Manifest
from .synthesis import EngineError, create_synthesizer


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
        prog="audiobook-creator",
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


def _project_root(config_path: Path) -> Path:
    return config_path.parent


def _manifest_path(config_path: Path) -> Path:
    return _project_root(config_path) / MANIFEST_FILENAME


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


def _apply_overrides(settings: Settings, args: argparse.Namespace) -> bool:
    """Copy any explicitly supplied CLI flags into ``settings``."""
    changed = False
    for attr in SETTING_ATTRS:
        value = getattr(args, attr, None)
        if value is None:
            continue
        changed = True
        if attr in ("voice", "speed", "pitch_shift", "pitch_range", "seed"):
            setattr(settings.voice, attr, value)
        elif attr in ("chapter_gap_ms", "sample_rate", "output_format", "mp3_bitrate"):
            setattr(settings.audio, attr, value)
        elif attr in ("chapters_per_file", "name_pattern"):
            setattr(settings.output_options, attr, value)
        elif attr == "checkpoint":
            settings.checkpoint = Path(value).expanduser().resolve()
        elif attr == "output":
            settings.output = Path(value).expanduser().resolve()
        else:
            setattr(settings, attr, value)
    return changed


def _safe(value: object) -> str:
    """Make a value safe to use inside a filename."""
    return str(value).replace("/", "_").replace("\\", "_")


def _destination_for(
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
    chapter_range = "all" if chapter_numbers is None else f"{chapter_numbers[0]:03d}-{chapter_numbers[1]:03d}"
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


def _describe_plan(settings: Settings, chapter_count: int) -> str:
    groups = split_groups(
        list(range(1, chapter_count + 1)), settings.output_options.chapters_per_file
    )
    if settings.output_options.chapters_per_file <= 0:
        return f"one file with all {chapter_count} chapter(s)"
    sizes = ", ".join(str(len(group)) for group in groups)
    return f"{len(groups)} file(s) with {sizes} chapter(s)"


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

    # EPUB sources can supply the title and author from their metadata.
    source_info = load_source(source)
    title = args.title or source_info.title
    author = args.author or source_info.author
    if not title:
        print("a title is required (--title, or EPUB metadata)", file=sys.stderr)
        return 1
    # Keep the resolved metadata values from being overwritten by empty
    # argparse defaults in _apply_overrides.
    args.title = title
    args.author = author

    # Store an absolute weights anchor when the assets exist relative to the
    # current directory, so they can live outside the project directory.
    # ONNX installs have no .pt file, hence the config/ONNX asset check.
    checkpoint = Path(args.checkpoint).expanduser() if args.checkpoint else Path("weights/tiny-voca.pt")
    weights_dir = checkpoint.parent
    has_onnx = (weights_dir / "config.json").is_file() and (
        weights_dir / "onnx" / "model.onnx"
    ).is_file()
    checkpoint_value = str(checkpoint.resolve()) if checkpoint.is_file() or has_onnx else str(checkpoint)

    settings = Settings(
        title=title,
        author=author,
        source=Path(local_source.name),
        chapter_pattern=getattr(args, "chapter_pattern", None) or Settings.chapter_pattern,
        checkpoint=Path(checkpoint_value),
        device=args.device or "cpu",
        output=Path("out/audiobook.wav"),
        audio_dir=Path("audio"),
    )
    _apply_overrides(settings, args)
    settings.validate()
    config_path.write_text(render_settings(settings), encoding="utf-8")
    Manifest(title=settings.title).save(_manifest_path(config_path))

    chapter_count = len(chapters_from_source(source_info, settings.chapter_pattern))
    print(f"Created project at {project}")
    print(f"Output plan: {_describe_plan(settings, chapter_count)}")
    print("Next: set up the engine (scripts/setup.sh), then:")
    print(f"  uv run audiobook-creator synthesize --config {config_path}")
    return 0


def command_config(args: argparse.Namespace) -> int:
    settings, config_path = load_settings(args.config)
    changed = _apply_overrides(settings, args)
    settings.validate()
    if changed:
        config_path.write_text(render_settings(settings, config_path.parent), encoding="utf-8")
        print(f"Updated {config_path}")

    try:
        chapters = chapters_from_source(load_source(settings.source), settings.chapter_pattern)
    except FileNotFoundError:
        chapters = []
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
    print(f"Chapters found: {len(chapters)}; output plan: {_describe_plan(settings, len(chapters))}")
    if chapters:
        split_mode = settings.output_options.chapters_per_file > 0
        preview_label = 1 if split_mode else "all"
        numbers = (1, min(len(chapters), settings.output_options.chapters_per_file)) if split_mode else None
        print(f"Example first output: {_destination_for(settings, preview_label, numbers)}")
    return 0


def command_synthesize(args: argparse.Namespace) -> int:
    settings, config_path = load_settings(args.config)
    _apply_overrides(settings, args)
    settings.validate()
    chapters = chapters_from_source(load_source(settings.source), settings.chapter_pattern)
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
        engine = create_synthesizer(settings).load()
    except (EngineError, RuntimeError) as exc:
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
        # Store the path relative to the project so assembly can find it.
        try:
            stored_path = str(target.relative_to(config_path.parent))
        except ValueError:  # audio_dir lies outside the project directory
            stored_path = str(target)
        manifest.mark_done(chapter.number, stored_path, duration)
        print(f"  [{chapter.number:>3}] {chapter.title!r} -> {target.name} ({duration:.1f}s)")

    manifest.save(_manifest_path(config_path))
    print(f"Done. {len(targets) - failed} synthesized, {failed} failed.")
    print(f"Assemble with: uv run audiobook-creator assemble --config {config_path}")
    return 1 if failed else 0


def command_assemble(args: argparse.Namespace) -> int:
    settings, config_path = load_settings(args.config)
    _apply_overrides(settings, args)
    settings.validate()

    manifest = Manifest.load(_manifest_path(config_path), settings.title)
    done_states = sorted(
        (state for state in manifest.chapters if state.status == "done" and state.file),
        key=lambda state: state.number,
    )
    files = manifest.completed_files(_project_root(config_path), settings.audio_dir)
    if not files:
        print("No synthesized chapters found. Run 'synthesize' first.", file=sys.stderr)
        return 1

    entries = [(state.number, path) for state, path in zip(done_states, files)]
    groups = split_groups(entries, settings.output_options.chapters_per_file)
    split_mode = settings.output_options.chapters_per_file > 0

    written = 0
    for part, group in enumerate(groups, start=1):
        numbers = (group[0][0], group[-1][0]) if split_mode else None
        label: str | int = part if split_mode else "all"
        destination = _destination_for(settings, label, numbers)
        try:
            assemble([path for _number, path in group], destination, settings.audio)
        except (ValueError, RuntimeError, OSError) as exc:
            print(f"Assembly failed: {exc}", file=sys.stderr)
            return 1
        chapter_note = f"chapters {numbers[0]}-{numbers[1]}" if numbers else f"all {len(group)} chapters"
        print(f"  {destination.name}: {chapter_note} ({len(group)} clip(s))")
        written += 1
    print(f"Wrote {written} audiobook file(s).")
    return 0


def command_status(args: argparse.Namespace) -> int:
    settings, config_path = load_settings(args.config)
    chapters = chapters_from_source(load_source(settings.source), settings.chapter_pattern)
    manifest = Manifest.load(_manifest_path(config_path), settings.title)
    manifest.sync(chapters)
    manifest.save(_manifest_path(config_path))

    done = sum(1 for c in manifest.chapters if c.status == "done")
    total_seconds = sum(c.duration_seconds or 0 for c in manifest.chapters)
    print(f"{settings.title}" + (f" — {settings.author}" if settings.author else ""))
    print(f"Chapters: {done}/{len(manifest.chapters)} synthesized ({total_seconds / 60:.1f} min)")
    print(f"Output plan: {_describe_plan(settings, len(manifest.chapters))}")
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
