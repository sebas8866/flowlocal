"""Unit tests for flowlocal.config validation, focused on the `theme` and
`save_history` fields added for the app window.

Run with: py -3.11 -m unittest discover -s tests
or:       py -3.11 -m unittest tests.test_config
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flowlocal.config import Config


class TestThemeValidation(unittest.TestCase):
    def test_default_theme_is_light(self):
        cfg = Config()
        self.assertEqual(cfg.theme, "light")

    def test_valid_themes_preserved(self):
        for value in ("light", "dark", "system"):
            cfg = Config(theme=value)
            cfg._validate()
            self.assertEqual(cfg.theme, value)

    def test_invalid_theme_resets_to_default(self):
        cfg = Config(theme="neon")
        cfg._validate()
        self.assertEqual(cfg.theme, "light")

    def test_non_string_theme_resets_to_default(self):
        cfg = Config(theme=123)
        cfg._validate()
        self.assertEqual(cfg.theme, "light")


class TestSaveHistoryValidation(unittest.TestCase):
    def test_default_save_history_is_true(self):
        cfg = Config()
        self.assertTrue(cfg.save_history)

    def test_valid_bool_preserved(self):
        cfg = Config(save_history=False)
        cfg._validate()
        self.assertFalse(cfg.save_history)

    def test_invalid_save_history_resets_to_default(self):
        cfg = Config(save_history="yes")
        cfg._validate()
        self.assertTrue(cfg.save_history)


class TestRoundTrip(unittest.TestCase):
    def test_load_tolerates_missing_new_fields(self):
        # Simulates loading an old config.json written before theme/
        # save_history existed: constructing with only legacy fields must
        # still produce valid defaults for the new ones.
        cfg = Config(trigger="mouse:x2")
        cfg._validate()
        self.assertEqual(cfg.theme, "light")
        self.assertTrue(cfg.save_history)


if __name__ == "__main__":
    unittest.main()
