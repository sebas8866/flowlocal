"""Unit tests for flowlocal.cloud (Groq cloud backend).

Run with: py -3.11 -m unittest discover -s tests
or:       py -3.11 -m unittest tests.test_cloud

No real network calls are made: flowlocal.cloud now reuses a module-level
`requests.Session` (see cloud._get_session / FIX 2) rather than calling
`requests.post`/`requests.get` directly, so these tests monkeypatch
`cloud._get_session` to return a `_FakeSession` whose `.post`/`.get` are the
test's fake functions.
"""
import io
import os
import sys
import unittest
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

from flowlocal import cloud
from flowlocal.cloud import CloudError


class _FakeSession:
    """Stand-in for requests.Session exposing only the methods a given test
    needs (post and/or get), matching the plain-function fakes' signatures
    (no `self`/session argument).
    """

    def __init__(self, post=None, get=None):
        if post is not None:
            self.post = post
        if get is not None:
            self.get = get


class FakeConfig:
    """Minimal stand-in for flowlocal.config.Config."""

    def __init__(
        self,
        groq_api_key="",
        cloud_stt_model="whisper-large-v3-turbo",
        cloud_llm_model="llama-3.3-70b-versatile",
        language=None,
        backend="cloud",
    ):
        self.groq_api_key = groq_api_key
        self.cloud_stt_model = cloud_stt_model
        self.cloud_llm_model = cloud_llm_model
        self.language = language
        self.backend = backend


class FakeResponse:
    def __init__(self, status_code=200, text="", json_data=None):
        self.status_code = status_code
        self.text = text
        self._json_data = json_data

    def json(self):
        if self._json_data is None:
            raise ValueError("no json")
        return self._json_data


class TestWavEncoding(unittest.TestCase):
    def test_encode_wav_produces_parseable_16bit_mono(self):
        import numpy as np

        audio = np.array([0.0, 0.5, -0.5, 1.0, -1.0], dtype="float32")
        wav_bytes = cloud._encode_wav(audio, 16000)

        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            self.assertEqual(wf.getnchannels(), 1)
            self.assertEqual(wf.getsampwidth(), 2)
            self.assertEqual(wf.getframerate(), 16000)
            self.assertEqual(wf.getnframes(), 5)
            frames = wf.readframes(5)

        samples = []
        for i in range(5):
            lo = frames[2 * i]
            hi = frames[2 * i + 1]
            val = (hi << 8) | lo
            if val >= 0x8000:
                val -= 0x10000
            samples.append(val)

        self.assertEqual(samples[0], 0)
        self.assertEqual(samples[1], int(0.5 * 32767))
        self.assertEqual(samples[2], int(-0.5 * 32767))
        self.assertEqual(samples[3], 32767)
        self.assertEqual(samples[4], -32767)

    def test_encode_wav_clips_out_of_range_samples(self):
        import numpy as np

        audio = np.array([2.0, -2.0], dtype="float32")
        wav_bytes = cloud._encode_wav(audio, 16000)

        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            frames = wf.readframes(2)

        lo, hi = frames[0], frames[1]
        val = (hi << 8) | lo
        self.assertEqual(val, 32767)  # clipped to +1.0 -> max positive


class TestMissingKey(unittest.TestCase):
    def test_transcribe_raises_when_key_missing(self):
        import numpy as np

        cfg = FakeConfig(groq_api_key="")
        audio = np.zeros(16000, dtype="float32")
        with self.assertRaises(CloudError):
            cloud.transcribe(audio, 16000, cfg)

    def test_clean_raises_when_key_missing(self):
        cfg = FakeConfig(groq_api_key="")
        with self.assertRaises(CloudError):
            cloud.clean("hello world", cfg)

    def test_check_returns_false_when_key_missing(self):
        cfg = FakeConfig(groq_api_key="")
        ok, msg = cloud.check(cfg)
        self.assertFalse(ok)
        self.assertTrue(msg)


class TestTranscribe(unittest.TestCase):
    def test_builds_correct_request_and_parses_text(self):
        import numpy as np

        cfg = FakeConfig(groq_api_key="secret-key", cloud_stt_model="whisper-large-v3-turbo",
                          language="en")
        audio = np.zeros(16000, dtype="float32")

        captured = {}

        def fake_post(url, headers=None, data=None, files=None, timeout=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["data"] = data
            captured["files"] = files
            captured["timeout"] = timeout
            return FakeResponse(status_code=200, text="  hello world  ")

        fake_session = _FakeSession(post=fake_post)
        orig_get_session = cloud._get_session
        cloud._get_session = lambda: fake_session
        try:
            result = cloud.transcribe(audio, 16000, cfg)
        finally:
            cloud._get_session = orig_get_session

        self.assertEqual(result, "hello world")
        self.assertEqual(captured["url"], cloud._GROQ_TRANSCRIPTIONS_URL)
        self.assertEqual(captured["headers"]["Authorization"], "Bearer secret-key")
        self.assertEqual(captured["data"]["model"], "whisper-large-v3-turbo")
        self.assertEqual(captured["data"]["response_format"], "text")
        self.assertEqual(captured["data"]["language"], "en")
        self.assertIn("file", captured["files"])
        self.assertEqual(captured["timeout"], 20)

    def test_omits_language_when_none(self):
        import numpy as np

        cfg = FakeConfig(groq_api_key="secret-key", language=None)
        audio = np.zeros(16000, dtype="float32")

        captured = {}

        def fake_post(url, headers=None, data=None, files=None, timeout=None):
            captured["data"] = data
            return FakeResponse(status_code=200, text="ok")

        fake_session = _FakeSession(post=fake_post)
        orig_get_session = cloud._get_session
        cloud._get_session = lambda: fake_session
        try:
            cloud.transcribe(audio, 16000, cfg)
        finally:
            cloud._get_session = orig_get_session

        self.assertNotIn("language", captured["data"])

    def test_forwards_prompt_field_when_given(self):
        import numpy as np

        cfg = FakeConfig(groq_api_key="secret-key")
        audio = np.zeros(16000, dtype="float32")

        captured = {}

        def fake_post(url, headers=None, data=None, files=None, timeout=None):
            captured["data"] = data
            return FakeResponse(status_code=200, text="ok")

        fake_session = _FakeSession(post=fake_post)
        orig_get_session = cloud._get_session
        cloud._get_session = lambda: fake_session
        try:
            cloud.transcribe(audio, 16000, cfg, prompt="Glossary: Aarav.")
        finally:
            cloud._get_session = orig_get_session

        self.assertEqual(captured["data"]["prompt"], "Glossary: Aarav.")

    def test_omits_prompt_field_when_not_given(self):
        import numpy as np

        cfg = FakeConfig(groq_api_key="secret-key")
        audio = np.zeros(16000, dtype="float32")

        captured = {}

        def fake_post(url, headers=None, data=None, files=None, timeout=None):
            captured["data"] = data
            return FakeResponse(status_code=200, text="ok")

        fake_session = _FakeSession(post=fake_post)
        orig_get_session = cloud._get_session
        cloud._get_session = lambda: fake_session
        try:
            cloud.transcribe(audio, 16000, cfg)
        finally:
            cloud._get_session = orig_get_session

        self.assertNotIn("prompt", captured["data"])

    def test_non_200_raises_cloud_error(self):
        import numpy as np

        cfg = FakeConfig(groq_api_key="secret-key")
        audio = np.zeros(16000, dtype="float32")

        def fake_post(url, headers=None, data=None, files=None, timeout=None):
            return FakeResponse(status_code=401, text="unauthorized")

        fake_session = _FakeSession(post=fake_post)
        orig_get_session = cloud._get_session
        cloud._get_session = lambda: fake_session
        try:
            with self.assertRaises(CloudError):
                cloud.transcribe(audio, 16000, cfg)
        finally:
            cloud._get_session = orig_get_session

    def test_empty_response_raises_cloud_error(self):
        import numpy as np

        cfg = FakeConfig(groq_api_key="secret-key")
        audio = np.zeros(16000, dtype="float32")

        def fake_post(url, headers=None, data=None, files=None, timeout=None):
            return FakeResponse(status_code=200, text="   ")

        fake_session = _FakeSession(post=fake_post)
        orig_get_session = cloud._get_session
        cloud._get_session = lambda: fake_session
        try:
            with self.assertRaises(CloudError):
                cloud.transcribe(audio, 16000, cfg)
        finally:
            cloud._get_session = orig_get_session

    def test_network_error_raises_cloud_error(self):
        import numpy as np

        cfg = FakeConfig(groq_api_key="secret-key")
        audio = np.zeros(16000, dtype="float32")

        def fake_post(*args, **kwargs):
            raise requests.exceptions.ConnectionError("boom")

        fake_session = _FakeSession(post=fake_post)
        orig_get_session = cloud._get_session
        cloud._get_session = lambda: fake_session
        try:
            with self.assertRaises(CloudError):
                cloud.transcribe(audio, 16000, cfg)
        finally:
            cloud._get_session = orig_get_session


class TestClean(unittest.TestCase):
    def test_builds_correct_request_and_parses_content(self):
        cfg = FakeConfig(groq_api_key="secret-key", cloud_llm_model="llama-3.3-70b-versatile")

        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            captured["timeout"] = timeout
            return FakeResponse(
                status_code=200,
                json_data={"choices": [{"message": {"content": "  Cleaned text.  "}}]},
            )

        fake_session = _FakeSession(post=fake_post)
        orig_get_session = cloud._get_session
        cloud._get_session = lambda: fake_session
        try:
            result = cloud.clean("um cleaned text", cfg)
        finally:
            cloud._get_session = orig_get_session

        self.assertEqual(result, "Cleaned text.")
        self.assertEqual(captured["url"], cloud._GROQ_CHAT_URL)
        self.assertEqual(captured["headers"]["Authorization"], "Bearer secret-key")
        self.assertEqual(captured["json"]["model"], "llama-3.3-70b-versatile")
        self.assertEqual(captured["json"]["temperature"], 0.2)
        self.assertEqual(len(captured["json"]["messages"]), 1)
        self.assertEqual(captured["json"]["messages"][0]["role"], "user")
        self.assertIn("um cleaned text", captured["json"]["messages"][0]["content"])
        self.assertEqual(captured["timeout"], 20)

    def test_non_200_raises_cloud_error(self):
        cfg = FakeConfig(groq_api_key="secret-key")

        def fake_post(url, headers=None, json=None, timeout=None):
            return FakeResponse(status_code=500)

        fake_session = _FakeSession(post=fake_post)
        orig_get_session = cloud._get_session
        cloud._get_session = lambda: fake_session
        try:
            with self.assertRaises(CloudError):
                cloud.clean("some text", cfg)
        finally:
            cloud._get_session = orig_get_session

    def test_forwards_app_context_and_previous_into_prompt(self):
        cfg = FakeConfig(groq_api_key="secret-key")

        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None):
            captured["json"] = json
            return FakeResponse(
                status_code=200,
                json_data={"choices": [{"message": {"content": "Cleaned text here."}}]},
            )

        fake_session = _FakeSession(post=fake_post)
        orig_get_session = cloud._get_session
        cloud._get_session = lambda: fake_session
        try:
            cloud.clean(
                "some dictated text",
                cfg,
                app_context="Discord.exe — #general",
                previous="earlier dictation tail",
            )
        finally:
            cloud._get_session = orig_get_session

        content = captured["json"]["messages"][0]["content"]
        self.assertIn("Discord.exe", content)
        self.assertIn("earlier dictation tail", content)

    def test_sanity_check_failure_raises_cloud_error(self):
        cfg = FakeConfig(groq_api_key="secret-key")

        def fake_post(url, headers=None, json=None, timeout=None):
            # Absurdly short rewrite of a long input should fail the shared
            # sanity check reused from flowlocal.cleaner.
            return FakeResponse(
                status_code=200,
                json_data={"choices": [{"message": {"content": "x"}}]},
            )

        fake_session = _FakeSession(post=fake_post)
        orig_get_session = cloud._get_session
        cloud._get_session = lambda: fake_session
        try:
            with self.assertRaises(CloudError):
                cloud.clean("this is a reasonably long piece of dictated text", cfg)
        finally:
            cloud._get_session = orig_get_session


class TestCheck(unittest.TestCase):
    def test_builds_correct_request(self):
        cfg = FakeConfig(groq_api_key="secret-key")

        captured = {}

        def fake_get(url, headers=None, timeout=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["timeout"] = timeout
            return FakeResponse(status_code=200)

        fake_session = _FakeSession(get=fake_get)
        orig_get_session = cloud._get_session
        cloud._get_session = lambda: fake_session
        try:
            ok, msg = cloud.check(cfg)
        finally:
            cloud._get_session = orig_get_session

        self.assertTrue(ok)
        self.assertEqual(msg, "Connected")
        self.assertEqual(captured["url"], cloud._GROQ_MODELS_URL)
        self.assertEqual(captured["headers"]["Authorization"], "Bearer secret-key")
        self.assertEqual(captured["timeout"], 10)

    def test_non_200_returns_false(self):
        cfg = FakeConfig(groq_api_key="secret-key")

        def fake_get(url, headers=None, timeout=None):
            return FakeResponse(status_code=401)

        fake_session = _FakeSession(get=fake_get)
        orig_get_session = cloud._get_session
        cloud._get_session = lambda: fake_session
        try:
            ok, msg = cloud.check(cfg)
        finally:
            cloud._get_session = orig_get_session

        self.assertFalse(ok)
        self.assertTrue(msg)

    def test_network_error_returns_false(self):
        cfg = FakeConfig(groq_api_key="secret-key")

        def fake_get(*args, **kwargs):
            raise requests.exceptions.ConnectionError("boom")

        fake_session = _FakeSession(get=fake_get)
        orig_get_session = cloud._get_session
        cloud._get_session = lambda: fake_session
        try:
            ok, msg = cloud.check(cfg)
        finally:
            cloud._get_session = orig_get_session

        self.assertFalse(ok)
        self.assertTrue(msg)


class TestSessionReuse(unittest.TestCase):
    """FIX 2: transcribe/clean/check must reuse the same module-level
    requests.Session rather than opening a new connection per call.
    """

    def setUp(self):
        self._orig_session = cloud._session

    def tearDown(self):
        cloud._session = self._orig_session

    def test_get_session_returns_same_object_across_calls(self):
        cloud._session = None
        first = cloud._get_session()
        second = cloud._get_session()
        self.assertIs(first, second)
        self.assertIsInstance(first, requests.Session)

    def test_transcribe_and_check_share_the_same_session(self):
        cloud._session = None

        seen_sessions = []
        orig_get_session = cloud._get_session

        def spying_get_session():
            session = orig_get_session()
            seen_sessions.append(session)
            return session

        cloud._get_session = spying_get_session
        try:
            import numpy as np

            cfg = FakeConfig(groq_api_key="secret-key")
            audio = np.zeros(16000, dtype="float32")

            real_session_post = requests.Session.post
            real_session_get = requests.Session.get
            requests.Session.post = lambda self, *a, **k: FakeResponse(status_code=200, text="ok")
            requests.Session.get = lambda self, *a, **k: FakeResponse(status_code=200)
            try:
                cloud.transcribe(audio, 16000, cfg)
                cloud.check(cfg)
            finally:
                requests.Session.post = real_session_post
                requests.Session.get = real_session_get
        finally:
            cloud._get_session = orig_get_session

        self.assertEqual(len(seen_sessions), 2)
        self.assertIs(seen_sessions[0], seen_sessions[1])


class TestConfigNewFields(unittest.TestCase):
    def test_accepts_new_fields(self):
        from flowlocal.config import Config

        cfg = Config(
            backend="cloud",
            groq_api_key="abc123",
            cloud_stt_model="whisper-large-v3-turbo",
            cloud_llm_model="llama-3.3-70b-versatile",
        )
        cfg._validate()
        self.assertEqual(cfg.backend, "cloud")
        self.assertEqual(cfg.groq_api_key, "abc123")
        self.assertEqual(cfg.cloud_stt_model, "whisper-large-v3-turbo")
        self.assertEqual(cfg.cloud_llm_model, "llama-3.3-70b-versatile")

    def test_invalid_backend_reverts_to_local(self):
        from flowlocal.config import Config

        cfg = Config(backend="not-a-real-backend")
        cfg._validate()
        self.assertEqual(cfg.backend, "local")

    def test_default_backend_is_local(self):
        from flowlocal.config import Config

        cfg = Config()
        self.assertEqual(cfg.backend, "local")
        self.assertEqual(cfg.groq_api_key, "")


if __name__ == "__main__":
    unittest.main()
