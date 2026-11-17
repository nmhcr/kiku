import unittest

from audiobook_creator.book import split_chapters


class SplitChaptersTest(unittest.TestCase):
    def test_splits_on_chapter_headings(self):
        text = (
            "Preamble line.\n\n"
            "Chapter One: The Start\nFirst body.\n\n"
            "Chapter Two\nSecond body.\n"
        )
        chapters = split_chapters(text)
        self.assertEqual([c.title for c in chapters], ["Prelude", "One: The Start", "Two"])
        self.assertEqual([c.number for c in chapters], [1, 2, 3])
        self.assertIn("First body.", chapters[1].text)
        self.assertIn("Second body.", chapters[2].text)

    def test_no_headings_gives_single_chapter(self):
        chapters = split_chapters("Just some text.\nMore text.")
        self.assertEqual(len(chapters), 1)
        self.assertEqual(chapters[0].title, "Full text")

    def test_empty_text(self):
        self.assertEqual(split_chapters("   \n "), [])

    def test_ignores_empty_chapters(self):
        text = "Chapter One\n\nChapter Two\nReal content here."
        chapters = split_chapters(text)
        self.assertEqual([c.title for c in chapters], ["Two"])


if __name__ == "__main__":
    unittest.main()
