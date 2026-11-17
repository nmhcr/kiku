import tempfile
import unittest
from pathlib import Path

from audiobook_creator.config import (
    render_config,
    settings_from_dict,
    load_settings,
    parse_chapters_per_file,
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

    def test_chapters_per_file_accepts_all(self):
        self.assertEqual(parse_chapters_per_file("all"), 0)
        self.assertEqual(parse_chapters_per_file("ALL"), 0)
        self.assertEqual(parse_chapters_per_file(10), 10)
        with self.assertRaises(ValueError):
            parse_chapters_per_file("many")

    def test_output_settings_from_toml(self):
        settings = settings_from_dict(
            {
                "book": {"title": "x"},
                "output": {"chapters_per_file": 10, "name_pattern": "{title} - {chapter}"},
            },
            Path("."),
        )
        self.assertEqual(settings.output_options.chapters_per_file, 10)
        self.assertEqual(settings.output_options.name_pattern, "{title} - {chapter}")
        self.assertEqual(settings.output_options.chapters_per_file, 10)

    def test_split_pattern_must_differ_per_file(self):
        with self.assertRaises(ValueError):
            settings_from_dict(
                {
                    "book": {"title": "x"},
                    "output": {"chapters_per_file": 10, "name_pattern": "{title}"},
                },
                Path("."),
            )

    def test_unknown_placeholder_rejected(self):
        with self.assertRaises(ValueError):
            settings_from_dict(
                {
                    "book": {"title": "x"},
                    "output": {"name_pattern": "{title} - {oops}"},
                },
                Path("."),
            )

    def test_render_includes_output_section(self):
        text = render_config("Book", chapters_per_file=10, name_pattern="{title} {chapter}")
        self.assertIn("[output]", text)
        self.assertIn("chapters_per_file = 10", text)
        self.assertIn('name_pattern = "{title} {chapter}"', text)


if __name__ == "__main__":
    unittest.main()
