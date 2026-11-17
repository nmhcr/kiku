import unittest
from pathlib import Path

from audiobook_creator.audio import split_groups
from audiobook_creator.cli import _destination_for
from audiobook_creator.config import Settings


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


if __name__ == "__main__":
    unittest.main()
