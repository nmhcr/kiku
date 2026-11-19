"""Thin, lazy wrapper around the tiny-voca inference API.

tiny-voca is imported only when the engine is actually used, so the rest of
the scaffold (config, book parsing, manifest, tests) works without the model
or its heavy dependencies installed.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from .config import Settings, VoiceSettings


class EngineError(RuntimeError):
    """Raised when the tiny-voca engine is missing or misconfigured."""


def create_synthesizer(settings: Settings):
    """Build the synthesizer for the configured backend.

    Both backends expose ``load()``, ``sample_rate`` and ``synthesize(text)``.
    """
    if settings.backend == "onnx":
        from .onnx_backend import OnnxSynthesizer

        return OnnxSynthesizer(settings.checkpoint, settings.voice, settings.device)
    return Synthesizer(settings.checkpoint, settings.voice, settings.device)


class Synthesizer:
    def __init__(
        self,
        checkpoint: str | Path,
        voice: VoiceSettings | None = None,
        device: str = "cpu",
    ) -> None:
        self.checkpoint = Path(checkpoint)
        self.voice = voice or VoiceSettings()
        self.device = device
        self._model = None

    def load(self) -> "Synthesizer":
        """Load the model, selecting the matching phonemizer front end."""
        if self._model is not None:
            return self
        if not self.checkpoint.is_file():
            raise EngineError(
                f"checkpoint not found: {self.checkpoint}\n"
                "Download the weights with scripts/setup.sh (or 'hf download "
                "n4ze3m/tiny-voca --local-dir weights')."
            )
        try:
            from tiny_voca import infer
        except ImportError as exc:
            raise EngineError(
                "tiny-voca is not installed. Install the engine with:\n"
                "  uv sync --extra engine --extra dev\n"
                "and set DIZIONE_DATA to the phonemizer directory (see README)."
            ) from exc

        try:
            # Match the checkpoint's front end unless the caller pinned one.
            if "TINY_VOCA_FRONTEND" not in os.environ:
                infer.match_frontend(self.checkpoint)
            self._model = infer.load_model(self.checkpoint, self.device)
        except Exception as exc:
            raise EngineError(
                f"could not load tiny-voca checkpoint: {exc}\n"
                "Check DIZIONE_DATA points at weights/phonemizer."
            ) from exc
        return self

    @property
    def sample_rate(self) -> int:
        if self._model is None:
            raise EngineError("engine is not loaded; call load() first")
        return int(self._model.config.sample_rate)

    def synthesize(self, text: str) -> np.ndarray:
        """Synthesize text of any length into a mono float waveform."""
        if self._model is None:
            raise EngineError("engine is not loaded; call load() first")
        from tiny_voca.infer import speak_text

        wave, _spoken = speak_text(
            self._model,
            text,
            voice=self.voice.voice,
            speed=self.voice.speed,
            pitch_shift=self.voice.pitch_shift,
            pitch_range=self.voice.pitch_range,
            seed=self.voice.seed,
        )
        return wave.numpy()
