"""Text handling shared by the synthesis backends.

These mirror ``tiny_voca.infer`` so the ONNX backend does not have to import
torch. Keeping them here also means both backends split text identically.
"""

from __future__ import annotations

import re

import numpy as np

PEAK = 0.99
ENGLISH_ONLY = "tiny-voca only reads English for now"

_SPACE = re.compile("[\\s" + chr(0xFEFF) + "]+")
_SENTENCE_END = re.compile(r"""(?:(?<=[.!?…])|(?<=[.!?…]["')\]]))\s+(?=["'(\[]?[A-Z0-9])""")
_NOT_AN_END = re.compile(
    r"(?:\b(?:mr|mrs|ms|dr|prof|sr|jr|st|vs|etc|no|inc|ltd|co|e\.g|i\.e|a\.m|p\.m)|\b[A-Z])\.$",
    re.IGNORECASE | re.ASCII,
)
_CLAUSE_END = re.compile(r"(?<=[,;:—])\s+")
_TEXT_END = re.compile("[\\s" + chr(0xFEFF) + "]+$")
_CLOSING = "\"'”’)]»"


def sentences(text: str) -> list[str]:
    """Split text into sentences without breaking after titles or initials."""
    parts: list[str] = []
    current = ""
    for piece in _SENTENCE_END.split(_SPACE.sub(" ", text).strip()):
        current = f"{current} {piece}" if current else piece
        if not _NOT_AN_END.search(current):
            parts.append(current)
            current = ""
    if current:
        parts.append(current)
    return [part for part in parts if any(char.isalnum() for char in part)]


def finished(text: str) -> str:
    """Add a full stop when the text ends without a mark."""
    body = _TEXT_END.sub("", text)
    if not body:
        return text
    last = _SPACE.split(body)[-1]
    if any(char.isalnum() for char in last):
        marked = last.rstrip(_CLOSING)[-1:] in tuple(".!?…,;:")
    else:
        marked = any(char in ".!?…" for char in last) or last in (",", ";", ":")
    return text if marked else body + "."


def shorter(text: str) -> list[str]:
    """Cut a long sentence at clauses, or in two between words."""
    pieces = _CLAUSE_END.split(text)
    if len(pieces) > 1:
        return pieces
    words = text.split(" ")
    if len(words) < 2:
        return []
    half = (len(words) + 1) // 2
    return [" ".join(words[:half]), " ".join(words[half:])]


def limit_peak(wave: np.ndarray, peak: float = PEAK) -> np.ndarray:
    """Scale audio down when its peak would clip as 16-bit PCM."""
    loudest = float(np.abs(wave).max()) if wave.size else 0.0
    return wave * (peak / loudest) if loudest > peak else wave
