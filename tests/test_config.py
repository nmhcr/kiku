import tempfile
import unittest
from pathlib import Path

from audiobook_creator.config import (
    render_config,
    settings_from_dict,
    load_settings,
    CONFIG_FILENAME,
)


class ConfigTest(unittest.TestCase):
    def test_defaults_and_path_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data = {"book": {"title": "Test Book"}}
            settings = settings_from_dict(data, base)
            self.assertEqual(settings.title, "Test Book")
            self.assertEqual(settings.voice.voice, "F2")
            self.assertEqual(settings.source, base / "book.txt")
            self.assertEqual(settings.checkpoint, base / "weights/tiny-voca.pt")

    def test_round_trip_rendered_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / CONFIG_FILENAME
            config.write_text(
                render_config("A Title", author="An Author", source="my book.txt"),
                encoding="utf-8",
            )
            settings, path = load_settings(config)
            self.assertEqual(path, config)
            self.assertEqual(settings.title, "A Title")
            self.assertEqual(settings.author, "An Author")
            self.assertEqual(settings.source, Path(tmp) / "my book.txt")

    def test_invalid_voice(self):
        with self.assertRaises(ValueError):
            settings_from_dict({"book": {"title": "x"}, "voice": {"voice": "X"}}, Path("."))


if __name__ == "__main__":
    unittest.main()
