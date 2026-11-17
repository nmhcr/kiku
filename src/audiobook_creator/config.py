"""Configuration for an audiobook project.

A project is a directory containing ``book.toml``, the source text, an
``audio/`` directory for per-chapter clips and an ``out/`` directory for the
assembled audiobook. All relative paths in the config are resolved against
the directory that holds the config file.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_FILENAME = "book.toml"

# Matches "Chapter 1", "## Chapter One: ...", "Part II", "Book 3", etc.
DEFAULT_CHAPTER_PATTERN = (
    r"^\s*(?:#{1,3}\s*)?(?:chapter|part|book)\s+([^\n]+?)\s*$"
)


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


@dataclass
class Settings:
    title: str
    author: str = ""
    source: Path = Path("book.txt")
    chapter_pattern: str = DEFAULT_CHAPTER_PATTERN
    checkpoint: Path = Path("weights/tiny-voca.pt")
    device: str = "cpu"
    audio_dir: Path = Path("audio")
    output: Path = Path("out/audiobook.wav")
    voice: VoiceSettings = field(default_factory=VoiceSettings)
    audio: AudioSettings = field(default_factory=AudioSettings)

    def validate(self) -> None:
        if not self.title.strip():
            raise ValueError("book.title cannot be empty")
        self.voice.validate()
        self.audio.validate()


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

    settings = Settings(
        title=str(book.get("title", "")),
        author=str(book.get("author", "")),
        source=_resolve(source.get("path", "book.txt"), base),
        chapter_pattern=str(source.get("chapter_pattern", DEFAULT_CHAPTER_PATTERN)),
        checkpoint=_resolve(engine.get("checkpoint", "weights/tiny-voca.pt"), base),
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
    )
    settings.validate()
    return settings


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


def render_config(
    title: str,
    author: str = "",
    source: str = "book.txt",
    checkpoint: str = "weights/tiny-voca.pt",
    voice: str = "F2",
) -> str:
    """Render a commented starter ``book.toml``."""
    return f"""# Audiobook project configuration.
# Relative paths are resolved against the directory containing this file.

[book]
title = {_toml_string(title)}
author = {_toml_string(author)}

[source]
path = {_toml_string(source)}
# A line starting a chapter. Group 1 is used as the chapter title.
chapter_pattern = {_toml_string(DEFAULT_CHAPTER_PATTERN)}

[engine]
# tiny-voca inference checkpoint. See scripts/setup.sh to download it.
checkpoint = {_toml_string(checkpoint)}
device = "cpu"

[voice]
# "F2" (female) or "M2" (male). tiny-voca is English only.
voice = {_toml_string(voice)}
speed = 1.0
pitch_shift = 0.0
pitch_range = 1.0
seed = 0

[audio]
sample_rate = 24000
chapter_gap_ms = 700
output_format = "wav"   # "wav" or "mp3" (mp3 requires ffmpeg)
mp3_bitrate = "128k"

[paths]
audio_dir = "audio"
output = "out/audiobook.wav"
"""
