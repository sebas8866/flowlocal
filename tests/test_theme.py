"""Unit tests for pure functions in flowlocal.ui.theme.

flowlocal.ui.theme has no module-level customtkinter import, so it's safe
to import headlessly in any environment (no Tk/CTk dependency needed).

Run with: py -3.11 -m unittest discover -s tests
or:       py -3.11 -m unittest tests.test_theme
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flowlocal.ui import theme


class TestFormatCount(unittest.TestCase):
    def test_under_thousand_returned_as_plain_int(self):
        self.assertEqual(theme.format_count(0), "0")
        self.assertEqual(theme.format_count(5), "5")
        self.assertEqual(theme.format_count(999), "999")

    def test_thousands_abbreviated_with_k(self):
        self.assertEqual(theme.format_count(1000), "1K")
        self.assertEqual(theme.format_count(22800), "22.8K")
        self.assertEqual(theme.format_count(179000), "179K")

    def test_millions_abbreviated_with_m(self):
        self.assertEqual(theme.format_count(1_000_000), "1M")
        self.assertEqual(theme.format_count(1_350_000), "1.4M")

    def test_trailing_zero_stripped(self):
        self.assertEqual(theme.format_count(5000), "5K")
        self.assertEqual(theme.format_count(5500), "5.5K")

    def test_negative_values(self):
        self.assertEqual(theme.format_count(-1500), "-1.5K")
        self.assertEqual(theme.format_count(-5), "-5")

    def test_non_numeric_input_defaults_to_zero(self):
        self.assertEqual(theme.format_count(None), "0")
        self.assertEqual(theme.format_count("abc"), "0")

    def test_float_input(self):
        self.assertEqual(theme.format_count(22849.0), "22.8K")


if __name__ == "__main__":
    unittest.main()
