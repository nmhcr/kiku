import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from audiobook_creator.config import Settings, VoiceSettings
from audiobook_creator.onnx_backend import OnnxSynthesizer
from audiobook_creator.synthesis import create_synthesizer

REPO = Path(__file__).resolve().parents[1]
WEIGHTS = REPO / "weights"
HAS_ONNX = (
    importlib.util.find_spec("onnxruntime") is not None
    and (WEIGHTS / "onnx" / "model.onnx").is_file()
    and (WEIGHTS / "config.json").is_file()
    and (WEIGHTS / "phonemizer").is_dir()
)


class BackendSelectionTest(unittest.TestCase):
    def test_default_is_onnx(self):
        settings = Settings(title="x")
        self.assertEqual(settings.backend, "onnx")
        self.assertIsInstance(create_synthesizer(settings), OnnxSynthesizer)

    def test_invalid_backend_rejected(self):
        from audiobook_creator.config import settings_from_dict

        with self.assertRaises(ValueError):
            settings_from_dict({"book": {"title": "x"}, "engine": {"backend": "x"}}, Path("."))


class PhonemizerDetectionTest(unittest.TestCase):
    def test_detected_from_weights_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            weights = Path(tmp) / "weights"
            (weights / "phonemizer").mkdir(parents=True)
            settings = Settings(title="x", checkpoint=weights / "tiny-voca.pt")
            with patch.dict(os.environ, {}, clear=True):
                create_synthesizer(settings)
                self.assertEqual(
                    os.environ["DIZIONE_DATA"], str((weights / "phonemizer").resolve())
                )

    def test_explicit_env_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            weights = Path(tmp) / "weights"
            (weights / "phonemizer").mkdir(parents=True)
            settings = Settings(title="x", checkpoint=weights / "tiny-voca.pt")
            with patch.dict(os.environ, {"DIZIONE_DATA": "/custom/data"}):
                create_synthesizer(settings)
                self.assertEqual(os.environ["DIZIONE_DATA"], "/custom/data")


@unittest.skipUnless(HAS_ONNX, "ONNX Runtime or ONNX weights are not installed")
class OnnxBackendTest(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"DIZIONE_DATA": str(WEIGHTS / "phonemizer")})
        self.env.start()
        self.synth = OnnxSynthesizer(WEIGHTS / "tiny-voca.pt", VoiceSettings()).load()

    def tearDown(self):
        self.env.stop()

    def test_sample_rate(self):
        self.assertEqual(self.synth.sample_rate, 24000)

    def test_synthesize_short_text(self):
        wave = self.synth.synthesize("Hello! This is a test.")
        self.assertGreater(len(wave), 24000)
        self.assertLessEqual(float(abs(wave).max()), 0.991)

    def test_long_text_is_split_into_utterances(self):
        text = "This is sentence number one. " * 40
        wave = self.synth.synthesize(text)
        self.assertGreater(len(wave) / self.synth.sample_rate, 40)

    def test_unknown_voice(self):
        with self.assertRaises(RuntimeError):
            OnnxSynthesizer(WEIGHTS / "tiny-voca.pt", VoiceSettings(voice="X")).load()


if __name__ == "__main__":
    unittest.main()
