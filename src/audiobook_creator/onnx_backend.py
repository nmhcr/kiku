"""ONNX Runtime synthesis backend.

Runs tiny-voca's pre-exported ONNX graph on CPU with a small runtime. Text is
phonemized with dizione as in the torch path; the graph receives phoneme IDs
and stress values and returns the waveform.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import numpy as np

from .config import VoiceSettings
from .speech_text import ENGLISH_ONLY, finished, limit_peak, sentences, shorter


class OnnxSynthesizer:
    """Same interface as the torch :class:`Synthesizer`."""

    def __init__(
        self,
        checkpoint: str | Path,
        voice: VoiceSettings | None = None,
        device: str = "cpu",
    ) -> None:
        self.checkpoint = Path(checkpoint)
        self.voice = voice or VoiceSettings()
        self.device = device
        # The checkpoint path anchors the weights directory, which also holds
        # config.json and onnx/model.onnx.
        weights_dir = self.checkpoint.parent
        self.model_path = weights_dir / "onnx" / "model.onnx"
        self.config_path = weights_dir / "config.json"
        self._session: Any = None
        self._config: dict[str, Any] = {}
        self._phonemize: Any = None

    def load(self) -> "OnnxSynthesizer":
        if not self.model_path.is_file() or not self.config_path.is_file():
            raise RuntimeError(
                f"ONNX assets not found under {self.checkpoint.parent}\n"
                "Download them with scripts/setup.sh (or hf download "
                "n4ze3m/tiny-voca --include 'onnx/model.onnx' --include 'config.json' "
                "--include 'phonemizer/*' --local-dir weights)."
            )
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise RuntimeError(
                "ONNX Runtime is not installed. Install it with: uv sync --extra onnx"
            ) from exc
        try:
            import dizione
        except ImportError as exc:
            raise RuntimeError(
                "dizione is not installed. Install it with: uv sync --extra onnx"
            ) from exc
        if not os.environ.get("DIZIONE_DATA"):
            raise RuntimeError(
                "dizione data not found: neither DIZIONE_DATA nor "
                f"{self.checkpoint.parent / 'phonemizer'} is available"
            )

        self._config = json.loads(self.config_path.read_text(encoding="utf-8"))
        voices = self._config.get("voices", ["F2", "M2"])
        if self.voice.voice not in voices:
            raise RuntimeError(
                f"unknown voice {self.voice.voice!r}; choose one of {', '.join(voices)}"
            )
        self._voice_index = voices.index(self.voice.voice)
        providers = ["CPUExecutionProvider"]
        self._session = ort.InferenceSession(str(self.model_path), providers=providers)
        self._phonemize = dizione.phonemize
        return self

    @property
    def sample_rate(self) -> int:
        if not self._config:
            raise RuntimeError("ONNX engine is not loaded; call load() first")
        return int(self._config.get("sample_rate", 24000))

    def _utterances(self, text: str, limit: int) -> list[Any]:
        """Phonemize text into utterances of at most ``limit`` tokens."""
        if not any(char.isalnum() for char in text):
            return []
        try:
            phones = self._phonemize(text)
        except ValueError:
            return []
        if len(phones.ids) <= limit:
            return [phones]
        pieces = shorter(text)
        if not pieces:
            raise ValueError(f"one word is too long to speak: {text[:40]!r}")
        result = []
        for piece in pieces:
            result.extend(self._utterances(piece, limit))
        return result

    def _run(self, phones: Any) -> np.ndarray:
        one = lambda value: np.array([value], dtype=np.float32)
        audio, _durations = self._session.run(
            None,
            {
                "tokens": np.array([phones.ids], dtype=np.int64),
                "stress": np.array([phones.stress], dtype=np.int64),
                "voice": np.array([self._voice_index], dtype=np.int64),
                "speed": one(self.voice.speed),
                "pitch_shift": one(self.voice.pitch_shift),
                "pitch_range": one(self.voice.pitch_range),
            },
        )
        wave = np.asarray(audio[0], dtype=np.float32)
        if not np.isfinite(wave).all():
            raise RuntimeError("model produced non-finite audio")
        return wave

    def synthesize(self, text: str) -> np.ndarray:
        if self._session is None:
            raise RuntimeError("ONNX engine is not loaded; call load() first")
        limit = int(self._config.get("model", {}).get("max_tokens", 512)) - 12
        waves: list[np.ndarray] = []
        for sentence in sentences(finished(text)):
            for phones in self._utterances(sentence, limit):
                waves.append(self._run(phones))
        if not waves:
            raise ValueError(f"nothing in the text could be read: {ENGLISH_ONLY}")
        return limit_peak(np.concatenate(waves))
