"""Matching what the camera read against the equipment list."""
import unittest

from app.identify import FrameReader, make_qr, match_qr, match_serial, normalise

UNITS = [{"id": "P-101", "serial": "HF30A-2026-0417"}] + [
    {"id": f"PCP-{n}", "serial": f"21031004-{n}"} for n in (1, 2, 3, 4)]


def ids(units):
    return [u["id"] for u in units]


class Serial(unittest.TestCase):
    def test_exact_serial_inside_a_nameplate_line(self):
        match, near = match_serial(["AMS PCP60S12/3 01 Ex B1 HNBR DTS", "S/N 21031004-3", "2023"], UNITS)
        self.assertEqual((match["id"], near), ("PCP-3", []))

    def test_look_alike_characters_are_folded(self):
        match, _ = match_serial(["SN: HF3OA-2O26-O4I7"], UNITS)          # O for 0, I for 1
        self.assertEqual(match["id"], "P-101")

    def test_spaces_and_split_lines(self):
        self.assertEqual(match_serial(["2103 1004 - 2"], UNITS)[0]["id"], "PCP-2")
        self.assertEqual(match_serial(["21031004", "-4"], UNITS)[0]["id"], "PCP-4")

    def test_one_wrong_character_is_only_a_candidate(self):
        match, near = match_serial(["S/N 21031004-7"], UNITS)            # last digit misread
        self.assertIsNone(match)
        self.assertEqual(sorted(ids(near)), ["PCP-1", "PCP-2", "PCP-3", "PCP-4"])

    def test_model_alone_identifies_nothing(self):
        self.assertEqual(match_serial(["AMS PCP60S12/3 01 Ex B1 HNBR DTS"], UNITS), (None, []))

    def test_unrelated_text(self):
        self.assertEqual(match_serial(["DANGER HIGH VOLTAGE", "380 V 50 Hz"], UNITS), (None, []))

    def test_normalise(self):
        self.assertEqual(normalise(" hf30a–2026_0417 "), "HF30A-2026-0417")


class Qr(unittest.TestCase):
    def test_payload_forms(self):
        self.assertEqual(match_qr("EQV:PCP-2", UNITS)["id"], "PCP-2")
        self.assertEqual(match_qr("21031004-1", UNITS)["id"], "PCP-1")
        self.assertIsNone(match_qr("https://example.com", UNITS))

    def test_generated_code_is_readable(self):
        self.assertEqual(FrameReader().qr(make_qr("EQV:P-101")), "EQV:P-101")


if __name__ == "__main__":
    unittest.main()
