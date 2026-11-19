import unittest

import numpy as np

from audiobook_creator.speech_text import finished, limit_peak, sentences, shorter


class SpeechTextTest(unittest.TestCase):
    def test_sentences_keep_abbreviations_together(self):
        self.assertEqual(
            sentences("Dr. Smith left. He came back."),
            ["Dr. Smith left.", "He came back."],
        )

    def test_sentences_split_on_marks(self):
        self.assertEqual(sentences("One! Two? Three."), ["One!", "Two?", "Three."])

    def test_finished_adds_full_stop(self):
        self.assertEqual(finished("Hello there"), "Hello there.")
        self.assertEqual(finished("Hello there."), "Hello there.")
        self.assertEqual(finished("See you :)"), "See you :).")

    def test_shorter_cuts_at_clauses(self):
        pieces = shorter("first part, second part, third part")
        self.assertEqual(len(pieces), 3)

    def test_shorter_cuts_long_sentence_in_two(self):
        pieces = shorter("one two three four five six")
        self.assertEqual(pieces, ["one two three", "four five six"])

    def test_limit_peak(self):
        wave = np.array([0.0, 2.0, -1.0], dtype=np.float32)
        result = limit_peak(wave)
        self.assertAlmostEqual(float(np.abs(result).max()), 0.99)
        self.assertTrue(np.array_equal(limit_peak(np.array([0.1, 0.2])), np.array([0.1, 0.2])))


if __name__ == "__main__":
    unittest.main()
