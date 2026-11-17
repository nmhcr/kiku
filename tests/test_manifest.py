import tempfile
import unittest
from pathlib import Path

from audiobook_creator.book import Chapter
from audiobook_creator.manifest import Manifest


class ManifestTest(unittest.TestCase):
    def setUp(self):
        self.chapters = [Chapter(1, "One", "a"), Chapter(2, "Two", "b")]

    def test_sync_and_progress(self):
        manifest = Manifest(title="Book")
        manifest.sync(self.chapters)
        self.assertEqual(manifest.pending_numbers(), [1, 2])
        manifest.mark_done(1, "audio/chapter_0001.wav", 12.5)
        self.assertTrue(manifest.is_done(1))
        self.assertEqual(manifest.pending_numbers(), [2])

    def test_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            manifest = Manifest(title="Book")
            manifest.sync(self.chapters)
            manifest.mark_done(2, "audio/chapter_0002.wav", 3.0)
            manifest.save(path)

            loaded = Manifest.load(path, title="ignored")
            self.assertEqual(loaded.title, "Book")
            self.assertTrue(loaded.is_done(2))
            self.assertEqual(loaded.completed_files(Path(tmp)), [Path(tmp) / "audio/chapter_0002.wav"])

    def test_bare_filenames_resolve_against_audio_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            audio_dir = base / "audio"
            audio_dir.mkdir()
            (audio_dir / "chapter_0001.wav").write_bytes(b"x")
            manifest = Manifest(title="Book")
            manifest.sync(self.chapters)
            manifest.mark_done(1, "chapter_0001.wav", 1.0)
            self.assertEqual(
                manifest.completed_files(base, audio_dir),
                [audio_dir / "chapter_0001.wav"],
            )

    def test_root_relative_paths_are_used_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            clip = base / "audio" / "chapter_0001.wav"
            clip.parent.mkdir()
            clip.write_bytes(b"x")
            manifest = Manifest(title="Book")
            manifest.sync(self.chapters)
            manifest.mark_done(1, str(clip.relative_to(base)), 1.0)
            self.assertEqual(manifest.completed_files(base, base / "other"), [clip])

    def test_sync_keeps_status(self):
        manifest = Manifest(title="Book")
        manifest.sync(self.chapters)
        manifest.mark_done(1, "audio/chapter_0001.wav", 1.0)
        manifest.sync([Chapter(1, "Renamed", "a"), Chapter(2, "Two", "b")])
        self.assertTrue(manifest.is_done(1))
        self.assertEqual(manifest.chapters[0].title, "Renamed")


if __name__ == "__main__":
    unittest.main()
