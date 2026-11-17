import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf

from audiobook_creator.audio import assemble, split_groups, write_wav
from audiobook_creator.cli import _destination_for
from audiobook_creator.config import AudioSettings, Settings


class SplitGroupsTest(unittest.TestCase):
    def test_zero_means_one_group(self):
        self.assertEqual(split_groups(list(range(5)), 0), [[0, 1, 2, 3, 4]])

    def test_even_split(self):
        groups = split_groups(list(range(100)), 10)
        self.assertEqual(len(groups), 10)
        self.assertTrue(all(len(group) == 10 for group in groups))

    def test_remainder_group(self):
        groups = split_groups(list(range(77)), 10)
        self.assertEqual(len(groups), 8)
        self.assertEqual([len(group) for group in groups], [10] * 7 + [7])
        self.assertEqual(groups[-1], list(range(70, 77)))

    def test_group_larger_than_items(self):
        self.assertEqual(split_groups([1, 2, 3], 10), [[1, 2, 3]])

    def test_empty_items(self):
        self.assertEqual(split_groups([], 10), [])


class DestinationTest(unittest.TestCase):
    def settings(self, **kwargs):
        settings = Settings(title="My Book")
        for key, value in kwargs.items():
            if key in ("chapters_per_file", "name_pattern"):
                setattr(settings.output_options, key, value)
            elif key in ("output_format",):
                setattr(settings.audio, key, value)
            else:
                setattr(settings, key, value)
        return settings

    def test_single_file_uses_output_path(self):
        settings = self.settings(output=Path("out/audiobook.wav"))
        self.assertEqual(_destination_for(settings, "all", None), Path("out/audiobook.wav"))

    def test_split_with_pattern(self):
        settings = self.settings(
            output=Path("out/audiobook.wav"),
            chapters_per_file=10,
            name_pattern="{title} - {chapter}",
        )
        self.assertEqual(
            _destination_for(settings, 1, (1, 10)),
            Path("out/My Book - 1.wav"),
        )
        self.assertEqual(
            _destination_for(settings, 8, (71, 77)),
            Path("out/My Book - 8.wav"),
        )

    def test_all_label_with_pattern(self):
        settings = self.settings(
            output=Path("out/audiobook.wav"),
            name_pattern="{title} - {chapter}",
        )
        self.assertEqual(
            _destination_for(settings, "all", None),
            Path("out/My Book - all.wav"),
        )

    def test_range_placeholder(self):
        settings = self.settings(
            output=Path("out/audiobook.wav"),
            chapters_per_file=10,
            name_pattern="{title}_{range}",
        )
        self.assertEqual(
            _destination_for(settings, 8, (71, 77)),
            Path("out/My Book_071-077.wav"),
        )

    def test_split_without_pattern_appends_part(self):
        settings = self.settings(
            output=Path("out/audiobook.wav"), chapters_per_file=10
        )
        self.assertEqual(
            _destination_for(settings, 2, (11, 20)),
            Path("out/audiobook_part2.wav"),
        )

    def test_mp3_extension(self):
        settings = self.settings(
            output=Path("out/audiobook.wav"),
            output_format="mp3",
            name_pattern="{title} - {chapter}",
        )
        self.assertEqual(
            _destination_for(settings, "all", None),
            Path("out/My Book - all.mp3"),
        )


class AssembleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def clips(self, rate=24000):
        first = self.root / "one.wav"
        second = self.root / "two.wav"
        write_wav(first, np.zeros(rate, dtype="float32"), rate)  # 1 second
        write_wav(second, np.zeros(rate, dtype="float32"), rate)
        return [first, second]

    def test_streaming_length_and_gap(self):
        clips = self.clips()
        output = self.root / "book.wav"
        assemble(clips, output, AudioSettings(chapter_gap_ms=500))
        info = sf.info(str(output))
        samples, rate = sf.read(str(output))
        self.assertEqual(rate, 24000)
        self.assertEqual(info.subtype, "PCM_16")
        self.assertEqual(len(samples), 24000 + 12000 + 24000)

    def test_zero_gap(self):
        output = self.root / "book.wav"
        assemble(self.clips(), output, AudioSettings(chapter_gap_ms=0))
        samples, _rate = sf.read(str(output))
        self.assertEqual(len(samples), 48000)

    def test_sample_rate_mismatch_removes_partial_output(self):
        clips = self.clips()
        write_wav(clips[1], np.zeros(16000, dtype="float32"), 16000)
        output = self.root / "book.wav"
        with self.assertRaises(ValueError):
            assemble(clips, output, AudioSettings())
        self.assertFalse(output.exists())

    def test_empty_clips(self):
        with self.assertRaises(ValueError):
            assemble([], self.root / "book.wav", AudioSettings())

    def test_wav_size_guard(self):
        with patch("audiobook_creator.audio._WAV_DATA_LIMIT", 1):
            with self.assertRaises(ValueError):
                assemble(self.clips(), self.root / "book.wav", AudioSettings())


if __name__ == "__main__":
    unittest.main()
