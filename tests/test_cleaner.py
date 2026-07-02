"""Unit tests for flowlocal.cleaner stage-1 (pure stdlib) rules.

Run with: py -3.11 -m unittest discover -s tests
or:       py -3.11 -m unittest tests.test_cleaner

These tests must run with nothing installed beyond the stdlib and the
flowlocal package itself (flowlocal.config is stdlib-only; flowlocal.cleaner
only imports third-party packages lazily inside its stage-2 functions,
which these tests never call because clean_llm=False).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flowlocal.cleaner import clean
from flowlocal.config import DEFAULT_FILLER_WORDS


class FakeConfig:
    """Minimal stand-in for flowlocal.config.Config — clean() only needs
    attribute access, no real Config import required.
    """

    def __init__(
        self,
        clean_fillers=True,
        clean_llm=False,
        filler_words=None,
        ollama_model="qwen2.5:7b-instruct",
    ):
        self.clean_fillers = clean_fillers
        self.clean_llm = clean_llm
        self.filler_words = filler_words if filler_words is not None else list(
            DEFAULT_FILLER_WORDS
        )
        self.ollama_model = ollama_model


class TestCleanerStage1(unittest.TestCase):
    def test_filler_removal_and_stutter_dedupe(self):
        # Default filler list (data-model.md) does not include "so", so it
        # is preserved; um/like are removed, "send send"/"to to" dedupe.
        cfg = FakeConfig()
        text = "um so like, send send the invoice to to John tomorrow"
        result = clean(text, cfg)
        self.assertEqual(result, "So, send the invoice to John tomorrow.")

    def test_multi_word_fillers_you_know_and_i_mean(self):
        cfg = FakeConfig()
        text = "you know i mean we should leave soon"
        result = clean(text, cfg)
        self.assertEqual(result, "We should leave soon.")

    def test_stutter_dedupe_case_insensitive(self):
        cfg = FakeConfig()
        text = "Send Send the file"
        result = clean(text, cfg)
        self.assertEqual(result, "Send the file.")

    def test_stutter_dedupe_triple_repeat(self):
        cfg = FakeConfig()
        text = "go go go now"
        result = clean(text, cfg)
        self.assertEqual(result, "Go now.")

    def test_capitalization(self):
        cfg = FakeConfig()
        text = "hello there. how are you"
        result = clean(text, cfg)
        self.assertEqual(result, "Hello there. How are you.")

    def test_terminal_punctuation_added(self):
        cfg = FakeConfig()
        text = "this has no ending punctuation"
        result = clean(text, cfg)
        self.assertTrue(result.endswith("."))
        self.assertEqual(result, "This has no ending punctuation.")

    def test_terminal_punctuation_preserved(self):
        cfg = FakeConfig()
        text = "did you see that?"
        result = clean(text, cfg)
        self.assertEqual(result, "Did you see that?")

    def test_punctuation_artifact_cleanup_double_comma(self):
        cfg = FakeConfig()
        text = "so, , the plan is set"
        result = clean(text, cfg)
        self.assertNotIn(",,", result)
        self.assertNotIn(", ,", result)

    def test_punctuation_artifact_cleanup_leading_comma(self):
        cfg = FakeConfig()
        text = "um, we should go"
        result = clean(text, cfg)
        self.assertFalse(result.startswith(","))
        self.assertEqual(result, "We should go.")

    def test_punctuation_artifact_double_spaces(self):
        cfg = FakeConfig()
        text = "hello   there   friend"
        result = clean(text, cfg)
        self.assertNotIn("  ", result)

    def test_empty_input(self):
        cfg = FakeConfig()
        self.assertEqual(clean("", cfg), "")
        self.assertEqual(clean("   ", cfg), "")
        self.assertEqual(clean(None, cfg), "")

    def test_text_that_is_only_fillers(self):
        cfg = FakeConfig()
        text = "um uh like you know"
        result = clean(text, cfg)
        self.assertEqual(result, "")

    def test_clean_fillers_disabled_keeps_fillers(self):
        cfg = FakeConfig(clean_fillers=False)
        text = "um send the file"
        result = clean(text, cfg)
        self.assertIn("um", result.lower())

    def test_clean_llm_disabled_skips_stage2(self):
        # clean_llm=False must never attempt a network call; if it did,
        # this test would hang or raise in a sandboxed/offline test run.
        cfg = FakeConfig(clean_llm=False)
        text = "um so like, send send the invoice to to John tomorrow"
        result = clean(text, cfg)
        self.assertEqual(result, "So, send the invoice to John tomorrow.")

    def test_word_boundary_does_not_strip_substring_matches(self):
        cfg = FakeConfig()
        # "ah" is a filler; "Ahmed" and "chair" must not be mangled.
        text = "ah Ahmed sat in the chair"
        result = clean(text, cfg)
        self.assertIn("Ahmed", result)
        self.assertIn("chair", result)


if __name__ == "__main__":
    unittest.main()
