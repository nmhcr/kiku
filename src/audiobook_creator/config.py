"""Configuration for an audiobook project.

A project is a directory containing ``book.toml``, the source text, an
``audio/`` directory for per-chapter clips and an ``out/`` directory for the
assembled audiobook. All relative paths in the config are resolved against
the directory that holds the config file.
"""

from __future__ import annotations

import json
import string
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_FILENAME = "book.toml"

# Matches "Chapter 1", "## Chapter One: ...", "Part II", "Book 3", etc.
DEFAULT_CHAPTER_PATTERN = (
    r"^\s*(?:#{1,3}\s*)?(?:chapter|part|book)\s+([^\n]+?)\s*$"
)


def parse_chapters_per_file(value: Any) -> int:
    """Normalize ``chapters_per_file`` from TOML or the CLI.

    ``0``, ``"all"`` and ``""`` all mean "one file for the whole book".
    """
    if isinstance(value, str) and value.strip().lower() in ("all", ""):
        return 0
    try:
        count = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"chapters_per_file must be an integer or 'all', got {value!r}"
        ) from exc
    if count < 0:
        raise ValueError("chapters_per_file cannot be negative")
    return count


@dataclass
class VoiceSettings:
    voice: str = "F2"  # tiny-voca has two voices: F2 (female) and M2 (male)
    speed: float = 1.0
    pitch_shift: float = 0.0
    pitch_range: float = 1.0
    seed: int = 0

    def validate(self) -> None:
        if self.voice not in ("F2", "M2"):
            raise ValueError(f"unknown voice {self.voice!r}; choose F2 or M2")
        if self.speed <= 0:
            raise ValueError("speed must be greater than zero")
        if self.pitch_range <= 0:
            raise ValueError("pitch_range must be greater than zero")


@dataclass
class AudioSettings:
    sample_rate: int = 24000  # expected engine rate; clips override this on assembly
    chapter_gap_ms: int = 700
    output_format: str = "wav"  # "wav" or "mp3" (mp3 needs ffmpeg)
    mp3_bitrate: str = "128k"

    def validate(self) -> None:
        if self.output_format not in ("wav", "mp3"):
            raise ValueError("output_format must be 'wav' or 'mp3'")
        if self.chapter_gap_ms < 0:
            raise ValueError("chapter_gap_ms cannot be negative")
        if self.sample_rate <= 0:
            raise ValueError("sample_rate must be greater than zero")
        if not self.mp3_bitrate.strip():
            raise ValueError("mp3_bitrate cannot be empty")


@dataclass
class OutputSettings:
    """How assembled audio is split into files and named.

    ``chapters_per_file = 0`` produces one file for the whole book. Otherwise
    chapters are grouped, with a final short group when the count is not an
    exact multiple (77 chapters / 10 -> 8 files, last one holding 7).

    ``name_pattern`` may use ``{title}``, ``{chapter}`` (part number, or
    ``all`` for a single file) and ``{range}`` (chapter range, or ``all``).
    An empty pattern uses ``paths.output`` for a single file, or appends
    ``_partN`` to its stem when splitting.
    """

    chapters_per_file: int = 0
    name_pattern: str = ""

    def validate(self) -> None:
        if self.chapters_per_file < 0:
            raise ValueError("output.chapters_per_file cannot be negative")
        if not self.name_pattern:
            return
        fields = {
            name
            for _literal, name, _format_spec, _conversion in string.Formatter().parse(
                self.name_pattern
            )
            if name
        }
        unknown = fields - {"title", "chapter", "range"}
        if unknown:
            raise ValueError(
                "unknown output.name_pattern placeholder(s): "
                + ", ".join(sorted(unknown))
            )
        if self.chapters_per_file > 0 and not fields & {"chapter", "range"}:
            raise ValueError(
                "output.name_pattern must contain {chapter} or {range} when "
                "output.chapters_per_file > 0, otherwise files would overwrite each other"
            )


@dataclass
class Settings:
    title: str
    author: str = ""
    source: Path = Path("book.txt")
    chapter_pattern: str = DEFAULT_CHAPTER_PATTERN
    checkpoint: Path = Path("weights/tiny-voca.pt")
    backend: str = "onnx"  # "onnx" (default, lightweight) or "torch"
    device: str = "cpu"
    audio_dir: Path = Path("audio")
    output: Path = Path("out/audiobook.wav")
    voice: VoiceSettings = field(default_factory=VoiceSettings)
    audio: AudioSettings = field(default_factory=AudioSettings)
    output_options: OutputSettings = field(default_factory=OutputSettings)

    def validate(self) -> None:
        if not self.title.strip():
            raise ValueError("book.title cannot be empty")
        if self.backend not in ("onnx", "torch"):
            raise ValueError("engine.backend must be 'onnx' or 'torch'")
        self.voice.validate()
        self.audio.validate()
        self.output_options.validate()


def _resolve(value: str | Path, base: Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


def settings_from_dict(data: dict[str, Any], base: Path) -> Settings:
    """Build ``Settings`` from parsed TOML, resolving paths against ``base``."""
    book = data.get("book", {})
    source = data.get("source", {})
    engine = data.get("engine", {})
    paths = data.get("paths", {})
    voice = data.get("voice", {})
    audio = data.get("audio", {})
    output = data.get("output", {})

    settings = Settings(
        title=str(book.get("title", "")),
        author=str(book.get("author", "")),
        source=_resolve(source.get("path", "book.txt"), base),
        chapter_pattern=str(source.get("chapter_pattern", DEFAULT_CHAPTER_PATTERN)),
        checkpoint=_resolve(engine.get("checkpoint", "weights/tiny-voca.pt"), base),
        backend=str(engine.get("backend", "onnx")).lower(),
        device=str(engine.get("device", "cpu")),
        audio_dir=_resolve(paths.get("audio_dir", "audio"), base),
        output=_resolve(paths.get("output", "out/audiobook.wav"), base),
        voice=VoiceSettings(
            voice=str(voice.get("voice", "F2")),
            speed=float(voice.get("speed", 1.0)),
            pitch_shift=float(voice.get("pitch_shift", 0.0)),
            pitch_range=float(voice.get("pitch_range", 1.0)),
            seed=int(voice.get("seed", 0)),
        ),
        audio=AudioSettings(
            sample_rate=int(audio.get("sample_rate", 24000)),
            chapter_gap_ms=int(audio.get("chapter_gap_ms", 700)),
            output_format=str(audio.get("output_format", "wav")),
            mp3_bitrate=str(audio.get("mp3_bitrate", "128k")),
        ),
        output_options=OutputSettings(
            chapters_per_file=parse_chapters_per_file(
                output.get("chapters_per_file", 0)
            ),
            name_pattern=str(output.get("name_pattern", "")),
        ),
    )
    settings.validate()
    return settings


_VOICE_FIELDS = frozenset(("voice", "speed", "pitch_shift", "pitch_range", "seed"))
_AUDIO_FIELDS = frozenset(("chapter_gap_ms", "sample_rate", "output_format", "mp3_bitrate"))
_OUTPUT_FIELDS = frozenset(("chapters_per_file", "name_pattern"))


def apply_overrides(settings: Settings, values: dict[str, Any]) -> bool:
    """Apply a dict of setting changes (``None`` values are ignored).

    Both the CLI and the service layer use this, so overrides behave the same
    way whichever frontend sets them.
    """
    changed = False
    for attr, value in values.items():
        if value is None:
            continue
        changed = True
        if attr in _VOICE_FIELDS:
            setattr(settings.voice, attr, value)
        elif attr in _AUDIO_FIELDS:
            setattr(settings.audio, attr, value)
        elif attr in _OUTPUT_FIELDS:
            setattr(settings.output_options, attr, value)
        elif attr == "checkpoint":
            settings.checkpoint = Path(value).expanduser().resolve()
        elif attr == "output":
            settings.output = Path(value).expanduser().resolve()
        elif attr == "source":
            settings.source = Path(value).expanduser().resolve()
        else:
            setattr(settings, attr, value)
    return changed


def load_settings(path: str | Path = CONFIG_FILENAME) -> tuple[Settings, Path]:
    """Load a project config. Returns ``(settings, config_path)``."""
    config_path = Path(path).expanduser().resolve()
    if not config_path.is_file():
        raise FileNotFoundError(f"config not found: {config_path}")
    with config_path.open("rb") as handle:
        data = tomllib.load(handle)
    return settings_from_dict(data, config_path.parent), config_path


def _toml_string(value: str) -> str:
    """Quote a string as a TOML basic string (JSON escaping is compatible)."""
    return json.dumps(value)


def _display_path(path: Path, base: Path | None = None) -> str:
    if base is not None:
        try:
            return str(path.relative_to(base))
        except ValueError:
            pass
    return str(path)


def render_settings(settings: Settings, base: Path | None = None) -> str:
    """Render a complete, commented ``book.toml`` from ``settings``.

    When ``base`` is given, paths underneath it are written relative to it.
    """
    settings.validate()
    return f"""# Audiobook project configuration.
# Relative paths are resolved against the directory containing this file.

[book]
title = {_toml_string(settings.title)}
author = {_toml_string(settings.author)}

[source]
path = {_toml_string(_display_path(settings.source, base))}
# A line starting a chapter. Group 1 is used as the chapter title.
chapter_pattern = {_toml_string(settings.chapter_pattern)}

[engine]
# Backend: "onnx" (default, lightweight) or "torch".
backend = {_toml_string(settings.backend)}
# tiny-voca inference checkpoint (needed for the torch backend only).
checkpoint = {_toml_string(_display_path(settings.checkpoint, base))}
device = {_toml_string(settings.device)}

[voice]
# "F2" (female) or "M2" (male). tiny-voca is English only.
voice = {_toml_string(settings.voice.voice)}
speed = {settings.voice.speed}
pitch_shift = {settings.voice.pitch_shift}
pitch_range = {settings.voice.pitch_range}
seed = {settings.voice.seed}

[audio]
sample_rate = {settings.audio.sample_rate}
chapter_gap_ms = {settings.audio.chapter_gap_ms}
output_format = {_toml_string(settings.audio.output_format)}   # "wav" or "mp3" (mp3 requires ffmpeg)
mp3_bitrate = {_toml_string(settings.audio.mp3_bitrate)}

[output]
# 0 (or "all") = one file for the whole book. N = N chapters per file;
# the last file holds the remainder (77 / 10 -> 8 files, last with 7).
chapters_per_file = {settings.output_options.chapters_per_file}
# Placeholders: {{title}}, {{chapter}} (part number or "all"), {{range}} (or "all").
# Empty = use paths.output, or append _partN to its stem when splitting.
name_pattern = {_toml_string(settings.output_options.name_pattern)}

[paths]
audio_dir = {_toml_string(_display_path(settings.audio_dir, base))}
output = {_toml_string(_display_path(settings.output, base))}
"""


def render_config(
    title: str,
    author: str = "",
    source: str = "book.txt",
    checkpoint: str = "weights/tiny-voca.pt",
    voice: str = "F2",
    *,
    speed: float = 1.0,
    pitch_shift: float = 0.0,
    pitch_range: float = 1.0,
    seed: int = 0,
    sample_rate: int = 24000,
    chapter_gap_ms: int = 700,
    output_format: str = "wav",
    mp3_bitrate: str = "128k",
    chapters_per_file: int | str = 0,
    name_pattern: str = "",
    device: str = "cpu",
    audio_dir: str = "audio",
    output: str = "out/audiobook.wav",
) -> str:
    """Render a commented starter ``book.toml`` (used by ``init``)."""
    return render_settings(
        Settings(
            title=title,
            author=author,
            source=Path(source),
            checkpoint=Path(checkpoint),
            device=device,
            audio_dir=Path(audio_dir),
            output=Path(output),
            voice=VoiceSettings(voice, speed, pitch_shift, pitch_range, seed),
            audio=AudioSettings(sample_rate, chapter_gap_ms, output_format, mp3_bitrate),
            output_options=OutputSettings(
                parse_chapters_per_file(chapters_per_file), name_pattern
            ),
        )
    )
