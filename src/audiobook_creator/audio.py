"""Waveform file I/O and final audiobook assembly."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import TypeVar

import numpy as np
import soundfile as sf

from .config import AudioSettings

T = TypeVar("T")

# A classic WAV header stores the data size in 32 bits (~4 GB limit).
_WAV_DATA_LIMIT = 4_000_000_000


def split_groups(items: list[T], size: int) -> list[list[T]]:
    """Split ``items`` into consecutive groups of ``size``.

    ``size <= 0`` means one group containing everything. The final group is
    allowed to be shorter (77 items with size 10 -> 7 groups of 10 and one of 7).
    """
    if size <= 0:
        return [list(items)]
    return [items[index : index + size] for index in range(0, len(items), size)]


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


def _total_frames(paths: list[Path], gap_frames: int) -> int:
    """Sum clip lengths from file headers only (no audio is read)."""
    return sum(int(sf.info(str(path)).frames) for path in paths) + gap_frames * max(
        0, len(paths) - 1
    )


def assemble(
    chapter_paths: list[Path],
    destination: str | Path,
    audio: AudioSettings,
) -> Path:
    """Concatenate chapter clips with silence gaps into one audiobook file.

    Clips are streamed one at a time into an open soundfile, so peak memory is
    roughly one chapter plus one gap, regardless of the length of the book.
    """
    if not chapter_paths:
        raise ValueError("no chapter clips to assemble")

    destination = Path(destination)
    if audio.output_format == "mp3":
        # ffmpeg needs a file; stream into a temporary WAV, then convert it.
        target = destination.with_suffix(".tmp.wav")
    else:
        target = destination

    sample_rate = int(sf.info(str(chapter_paths[0])).samplerate)
    gap_frames = int(sample_rate * audio.chapter_gap_ms / 1000)
    if _total_frames(chapter_paths, gap_frames) * 2 > _WAV_DATA_LIMIT:
        raise ValueError(
            "the assembled audio would exceed the 4 GB WAV limit; "
            "use output_format = 'mp3' or set chapters_per_file to split the book"
        )
    gap = np.zeros(gap_frames, dtype="float32") if gap_frames else None

    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sf.SoundFile(
            str(target), mode="w", samplerate=sample_rate, channels=1, subtype="PCM_16"
        ) as output:
            for index, path in enumerate(chapter_paths):
                samples, rate = read_wav(path)
                if rate != sample_rate:
                    raise ValueError(
                        f"sample rate mismatch: {path.name} is {rate} Hz, expected {sample_rate} Hz"
                    )
                if samples.ndim != 1:
                    samples = samples.mean(axis=1)
                if index and gap is not None:
                    output.write(gap)
                output.write(samples)
    except Exception:
        target.unlink(missing_ok=True)  # do not leave a truncated audiobook behind
        raise

    if audio.output_format == "mp3":
        _convert_to_mp3(target, destination, audio.mp3_bitrate)
    return destination
