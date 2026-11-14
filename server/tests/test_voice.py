"""Turning a recognised phrase into a unit."""
import unittest

from app.voice import VOCABULARY, match_text, spoken_number, sure

UNITS = [{"id": "P-101", "name": "Насосный агрегат центробежный"}] + [
    {"id": f"PCP-{n}", "name": "Агрегат насосный одновинтовой эксцентриковый"} for n in (1, 2, 3, 4)]


def named(text):
    match, candidates = match_text(text, UNITS)
    return (match["id"] if match else None), [c["id"] for c in candidates]


class Numbers(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(spoken_number("насос сто один".split()), 101)
        self.assertEqual(spoken_number("один ноль один".split()), 101)
        self.assertEqual(spoken_number("номер двадцать три".split()), 23)
        self.assertEqual(spoken_number("агрегат третий".split()), 3)
        self.assertEqual(spoken_number("двенадцать".split()), 12)
        self.assertIsNone(spoken_number("насос винтовой".split()))


class Matching(unittest.TestCase):
    def test_number_names_the_unit(self):
        self.assertEqual(named("насос сто один"), ("P-101", []))
        self.assertEqual(named("винтовой номер три"), ("PCP-3", []))
        self.assertEqual(named("агрегат четвёртый"), ("PCP-4", []))

    def test_kind_alone(self):
        self.assertEqual(named("центробежный насос"), ("P-101", []))                  # the only one
        self.assertEqual(named("винтовой насос"), (None, ["PCP-1", "PCP-2", "PCP-3", "PCP-4"]))

    def test_nothing_usable(self):
        self.assertEqual(named(""), (None, []))
        self.assertEqual(named("насос"), (None, []))
        self.assertEqual(named("насос семь"), (None, []))                              # no such unit
        self.assertEqual(named("центробежный три"), (None, []))                        # the kind contradicts the number

    def test_only_a_clear_full_phrase_is_trusted(self):
        self.assertTrue(sure([("насос", 1.0), ("сто", 0.73), ("один", 1.0)]))
        self.assertFalse(sure([("третий", 1.0)]))                                      # a lone word may be noise
        self.assertFalse(sure([("винтовой", 1.0), ("номер", 0.9), ("четвёртый", 0.37)]))
        self.assertFalse(sure([]))

    def test_vocabulary_is_single_words(self):
        self.assertTrue(all(" " not in word for word in VOCABULARY))


if __name__ == "__main__":
    unittest.main()
