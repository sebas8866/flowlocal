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

from flowlocal.cleaner import (
    build_rewrite_prompt,
    clean,
    is_hallucination,
    is_undo_command,
)
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
        voice_commands=True,
        vocabulary=None,
    ):
        self.clean_fillers = clean_fillers
        self.clean_llm = clean_llm
        self.filler_words = filler_words if filler_words is not None else list(
            DEFAULT_FILLER_WORDS
        )
        self.ollama_model = ollama_model
        self.voice_commands = voice_commands
        self.vocabulary = vocabulary if vocabulary is not None else []


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


class TestNewlineVoiceCommands(unittest.TestCase):
    def test_leading_new_line_becomes_break(self):
        cfg = FakeConfig()
        result = clean("new line start the meeting notes here", cfg)
        self.assertIn("\n", result)

    def test_new_line_between_clauses_becomes_break(self):
        cfg = FakeConfig()
        result = clean("finish the report new line submit it by friday", cfg)
        self.assertIn("\n", result)

    def test_new_paragraph_becomes_double_break(self):
        cfg = FakeConfig()
        result = clean("new paragraph today we begin", cfg)
        self.assertIn("\n", result)

    def test_mid_phrase_new_line_of_products_unchanged(self):
        cfg = FakeConfig()
        result = clean("we are launching a new line of products", cfg)
        self.assertNotIn("\n", result)
        self.assertIn("new line of products", result.lower())

    def test_mid_phrase_new_line_item_unchanged(self):
        cfg = FakeConfig()
        result = clean("please add a new line item to the invoice", cfg)
        self.assertNotIn("\n", result)

    def test_voice_commands_disabled_leaves_text_unchanged(self):
        cfg = FakeConfig(voice_commands=False)
        result = clean("new line", cfg)
        self.assertNotIn("\n", result)
        self.assertIn("new line", result.lower())


class TestUndoCommandDetection(unittest.TestCase):
    def test_exact_matches(self):
        for phrase in ("scratch that", "delete that", "undo that", "undo last"):
            self.assertTrue(is_undo_command(phrase), phrase)

    def test_case_insensitive(self):
        self.assertTrue(is_undo_command("Scratch That"))
        self.assertTrue(is_undo_command("UNDO THAT"))

    def test_punctuation_variants(self):
        self.assertTrue(is_undo_command("scratch that."))
        self.assertTrue(is_undo_command("scratch that!"))
        self.assertTrue(is_undo_command("  undo last  "))

    def test_non_matches(self):
        self.assertFalse(is_undo_command("scratch that itch"))
        self.assertFalse(is_undo_command("please undo that change for me"))
        self.assertFalse(is_undo_command("hello world"))
        self.assertFalse(is_undo_command(""))
        self.assertFalse(is_undo_command(None))


class TestHallucinationGuard(unittest.TestCase):
    def test_exact_matches(self):
        for phrase in (
            "thank you",
            "thanks for watching",
            "thank you for watching",
            "thank you bye",
            "bye",
            "you",
            "subtitles by the amara org community",
            "please subscribe",
            "the end",
        ):
            self.assertTrue(is_hallucination(phrase), phrase)

    def test_case_and_punctuation_insensitive(self):
        self.assertTrue(is_hallucination("Thank You."))
        self.assertTrue(is_hallucination("THANK YOU FOR WATCHING!"))

    def test_near_misses_do_not_match(self):
        self.assertFalse(is_hallucination("thank you everyone for coming"))
        self.assertFalse(is_hallucination("thanks for watching my channel"))
        self.assertFalse(is_hallucination("bye for now, see you later"))
        self.assertFalse(is_hallucination("send the report to you"))

    def test_empty_input(self):
        self.assertFalse(is_hallucination(""))
        self.assertFalse(is_hallucination(None))


class TestRewritePromptBuilder(unittest.TestCase):
    def test_rules_and_few_shots_present(self):
        cfg = FakeConfig()
        prompt = build_rewrite_prompt("hello world", cfg)
        self.assertIn("You clean up voice-dictation transcripts.", prompt)
        self.assertIn("Remove filler words", prompt)
        self.assertIn("self-corrects or restarts", prompt)
        self.assertIn("Collapse stutters", prompt)
        self.assertIn("never paraphrase, formalize, shorten, or summarize", prompt)
        self.assertIn("Preserve ALL content", prompt)
        self.assertIn("new line", prompt)
        self.assertIn("no commentary, no surrounding", prompt)
        self.assertIn("Examples:", prompt)
        self.assertIn("john.smith@gmail.com", prompt)
        self.assertIn("hello world", prompt)

    def test_vocabulary_section_only_when_present(self):
        cfg_empty = FakeConfig(vocabulary=[])
        prompt_empty = build_rewrite_prompt("hi", cfg_empty)
        self.assertNotIn("Correct spellings", prompt_empty)

        cfg_with_vocab = FakeConfig(vocabulary=["Aarav", "Kubernetes"])
        prompt_with_vocab = build_rewrite_prompt("hi", cfg_with_vocab)
        self.assertIn("Correct spellings of names/terms the speaker uses:", prompt_with_vocab)
        self.assertIn("Aarav, Kubernetes", prompt_with_vocab)

    def test_app_context_section_only_when_provided(self):
        cfg = FakeConfig()
        prompt_none = build_rewrite_prompt("hi", cfg, app_context=None)
        self.assertNotIn("will be typed into", prompt_none)

        prompt_with_ctx = build_rewrite_prompt("hi", cfg, app_context="Discord.exe — #general")
        self.assertIn("will be typed into: Discord.exe", prompt_with_ctx)

    def test_previous_section_only_when_provided(self):
        cfg = FakeConfig()
        prompt_none = build_rewrite_prompt("hi", cfg, previous=None)
        self.assertNotIn("previous dictation", prompt_none)

        prompt_with_prev = build_rewrite_prompt("hi", cfg, previous="earlier text")
        self.assertIn("previous dictation moments ago", prompt_with_prev)
        self.assertIn("earlier text", prompt_with_prev)
        self.assertIn("do NOT repeat or rewrite it", prompt_with_prev)


class TestVocabularyConfigValidation(unittest.TestCase):
    def test_valid_vocabulary_kept(self):
        from flowlocal.config import Config

        cfg = Config(vocabulary=["Aarav", "Kubernetes"])
        cfg._validate()
        self.assertEqual(cfg.vocabulary, ["Aarav", "Kubernetes"])

    def test_non_list_vocabulary_reverts_to_empty(self):
        from flowlocal.config import Config

        cfg = Config(vocabulary="not-a-list")
        cfg._validate()
        self.assertEqual(cfg.vocabulary, [])

    def test_vocabulary_with_non_string_reverts_to_empty(self):
        from flowlocal.config import Config

        cfg = Config(vocabulary=["ok", 123])
        cfg._validate()
        self.assertEqual(cfg.vocabulary, [])

    def test_vocabulary_with_empty_string_reverts_to_empty(self):
        from flowlocal.config import Config

        cfg = Config(vocabulary=["ok", ""])
        cfg._validate()
        self.assertEqual(cfg.vocabulary, [])

    def test_default_vocabulary_is_empty_list(self):
        from flowlocal.config import Config

        cfg = Config()
        self.assertEqual(cfg.vocabulary, [])


class TestSmartContextConfigValidation(unittest.TestCase):
    def test_default_is_true(self):
        from flowlocal.config import Config

        cfg = Config()
        self.assertTrue(cfg.smart_context)

    def test_invalid_type_reverts_to_default(self):
        from flowlocal.config import Config

        cfg = Config(smart_context="yes")
        cfg._validate()
        self.assertTrue(cfg.smart_context)

    def test_explicit_false_kept(self):
        from flowlocal.config import Config

        cfg = Config(smart_context=False)
        cfg._validate()
        self.assertFalse(cfg.smart_context)


class TestVoiceCommandsConfigValidation(unittest.TestCase):
    def test_default_is_true(self):
        from flowlocal.config import Config

        cfg = Config()
        self.assertTrue(cfg.voice_commands)

    def test_invalid_type_reverts_to_default(self):
        from flowlocal.config import Config

        cfg = Config(voice_commands="yes")
        cfg._validate()
        self.assertTrue(cfg.voice_commands)

    def test_explicit_false_kept(self):
        from flowlocal.config import Config

        cfg = Config(voice_commands=False)
        cfg._validate()
        self.assertFalse(cfg.voice_commands)


class TestTranscriberInitialPromptForwarding(unittest.TestCase):
    """Stub-level test: verifies initial_prompt is forwarded into the
    faster-whisper transcribe() call without loading a real model.
    """

    def test_initial_prompt_forwarded_to_model_transcribe(self):
        from flowlocal.transcriber import Transcriber

        t = Transcriber("large-v3-turbo")

        captured = {}

        class FakeSegment:
            text = "hello"

        class FakeModel:
            def transcribe(self, audio_np, **kwargs):
                captured.update(kwargs)
                return [FakeSegment()], None

        t._model = FakeModel()
        t._device = "cpu"
        t._compute_type = "int8"

        import numpy as np

        audio = (np.random.default_rng(0).standard_normal(16000).astype("float32") * 0.1)
        t.transcribe(audio, language="en", initial_prompt="Glossary: Aarav.")

        self.assertEqual(captured.get("initial_prompt"), "Glossary: Aarav.")
        self.assertEqual(captured.get("condition_on_previous_text"), False)


class UndoHallucinationPhraseDisjointnessTest(unittest.TestCase):
    """FIX 6b: app._process now checks is_undo_command() before
    is_hallucination(), so an utterance must never be able to match both
    phrase sets — otherwise ordering would silently decide the outcome
    instead of the phrase sets being unambiguous by construction.
    """

    def test_undo_and_hallucination_phrases_are_disjoint(self):
        from flowlocal.cleaner import _HALLUCINATION_PHRASES, _UNDO_PHRASES

        self.assertEqual(_UNDO_PHRASES & _HALLUCINATION_PHRASES, set())


if __name__ == "__main__":
    unittest.main()
