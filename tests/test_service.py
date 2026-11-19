import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf

from audiobook_creator.service import (
    create_project,
    get_settings,
    get_status,
    list_chapters,
    preview_voice,
    synthesize,
    update_settings,
)
from audiobook_creator.service import assemble as assemble_service


class FakeEngine:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.sample_rate = 24000

    def load(self):
        return self

    def synthesize(self, text: str) -> np.ndarray:
        if self.fail:
            raise RuntimeError("engine failed")
        return np.full(24000, 0.1, dtype="float32")


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / "book.txt"
        self.source.write_text(
            "Chapter One\nHello world.\n\nChapter Two\nSecond text here.\n",
            encoding="utf-8",
        )
        self.project = self.root / "project"
        self.info = create_project(self.source, self.project, title="Test Book")
        self.config = self.info.config_path

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_project(self):
        self.assertTrue(self.config.is_file())
        self.assertTrue((self.project / "manifest.json").is_file())
        self.assertTrue((self.project / "book.txt").is_file())
        self.assertEqual([c.title for c in self.info.chapters], ["One", "Two"])
        self.assertEqual(len(self.info.output_plan.files), 1)

    def test_update_settings_persists(self):
        settings, changed = update_settings(
            self.config,
            {"voice": "M2", "chapters_per_file": 10, "name_pattern": "{title} - {chapter}"},
        )
        self.assertTrue(changed)
        self.assertEqual(settings.voice.voice, "M2")
        self.assertEqual(get_settings(self.config).voice.voice, "M2")
        self.assertEqual(get_settings(self.config).output_options.chapters_per_file, 10)

    def test_list_and_status(self):
        self.assertEqual(len(list_chapters(self.config)), 2)
        status = get_status(self.config)
        self.assertEqual(status.title, "Test Book")
        self.assertEqual(status.done, 0)
        self.assertEqual(len(status.output_plan.files), 1)

    def test_synthesize_updates_manifest_with_progress(self):
        events = []
        with patch(
            "audiobook_creator.service.create_synthesizer", return_value=FakeEngine()
        ):
            result = synthesize(self.config, progress=events.append)
        self.assertEqual((result.synthesized, result.failed, result.skipped), (2, 0, 0))
        self.assertEqual(result.sample_rate, 24000)
        self.assertEqual([event.state for event in events], ["started", "done", "started", "done"])
        self.assertEqual(get_status(self.config).done, 2)

    def test_synthesize_resumes(self):
        with patch("audiobook_creator.service.create_synthesizer", return_value=FakeEngine()):
            synthesize(self.config)
            second = synthesize(self.config)
        self.assertEqual((second.synthesized, second.skipped), (0, 2))

    def test_synthesize_failure_is_recorded(self):
        events = []
        with patch(
            "audiobook_creator.service.create_synthesizer", return_value=FakeEngine(fail=True)
        ):
            result = synthesize(self.config, progress=events.append)
        self.assertEqual(result.failed, 2)
        self.assertTrue(all(event.state == "failed" for event in events if event.state != "started"))
        self.assertEqual(get_status(self.config).failed, 2)

    def test_assemble_single_and_split(self):
        with patch("audiobook_creator.service.create_synthesizer", return_value=FakeEngine()):
            synthesize(self.config)
        written = assemble_service(self.config)
        self.assertEqual(len(written), 1)
        info = sf.info(str(written[0].path))
        self.assertAlmostEqual(info.duration, 2.7, places=1)  # 2s audio + 0.7s gap

        update_settings(
            self.config,
            {"chapters_per_file": 1, "name_pattern": "{title} - {chapter}"},
        )
        written = assemble_service(self.config)
        self.assertEqual([file.path.name for file in written], ["Test Book - 1.wav", "Test Book - 2.wav"])
        self.assertEqual([file.chapter_count for file in written], [1, 1])

    def test_preview_voice(self):
        with patch("audiobook_creator.service.create_synthesizer", return_value=FakeEngine()):
            wave, rate = preview_voice(self.config, "Hello")
        self.assertEqual(len(wave), 24000)
        self.assertEqual(rate, 24000)

    def test_assemble_without_audio_raises(self):
        with self.assertRaises(ValueError):
            assemble_service(self.config)


if __name__ == "__main__":
    unittest.main()
