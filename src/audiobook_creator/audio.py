"""Waveform file I/O and final audiobook assembly."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

from .config import AudioSettings


def write_wav(path: str | Path, samples: np.ndarray, sample_rate: int) -> None:
    """Write chapter audio as 16-bit PCM (tiny-voca already limits the peak)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    sf.write(target, samples, sample_rate, subtype="PCM_16")


def read_wav(path: str | Path) -> tuple[np.ndarray, int]:
    samples, sample_rate = sf.read(Path(path), dtype="float32", always_2d=False)
    return np.asarray(samples), int(sample_rate)


def _convert_to_mp3(wav_path: Path, mp3_path: Path, bitrate: str) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError(
            "ffmpeg is required for mp3 output; install it or use output_format = 'wav'"
        )
    mp3_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg, "-y", "-loglevel", "error",
            "-i", str(wav_path),
            "-codec:a", "libmp3lame", "-b:a", bitrate,
            str(mp3_path),
        ],
        check=True,
    )
    wav_path.unlink(missing_ok=True)


def assemble(
    chapter_paths: list[Path],
    destination: str | Path,
    audio: AudioSettings,
) -> Path:
    """Concatenate chapter clips with silence gaps into one audiobook file."""
    if not chapter_paths:
        raise ValueError("no chapter clips to assemble")

    pieces: list[np.ndarray] = []
    sample_rate: int | None = None
    gap: np.ndarray | None = None

    for index, path in enumerate(chapter_paths):
        samples, rate = read_wav(path)
        if samples.ndim != 1:
            samples = samples.mean(axis=1)  # downmix to mono
        if sample_rate is None:
            sample_rate = rate
            gap = np.zeros(int(rate * audio.chapter_gap_ms / 1000), dtype="float32")
        elif rate != sample_rate:
            raise ValueError(
                f"sample rate mismatch: {path.name} is {rate} Hz, expected {sample_rate} Hz"
            )
        if index:
            pieces.append(gap)  # type: ignore[arg-type]
        pieces.append(samples)

    assert sample_rate is not None
    waveform = np.concatenate(pieces)
    destination = Path(destination)
    if audio.output_format == "mp3":
        temp_wav = destination.with_suffix(".tmp.wav")
        write_wav(temp_wav, waveform, sample_rate)
        _convert_to_mp3(temp_wav, destination, audio.mp3_bitrate)
    else:
        write_wav(destination, waveform, sample_rate)
    return destination
