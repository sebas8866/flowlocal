"""Unit tests for flowlocal.cloud (Groq cloud backend).

Run with: py -3.11 -m unittest discover -s tests
or:       py -3.11 -m unittest tests.test_cloud

No real network calls are made: flowlocal.cloud imports `requests` lazily
inside its functions, so these tests monkeypatch the module attribute
`requests.post` / `requests.get` after importing `requests` themselves (a
real third-party dependency of this project, already installed in .venv).
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

        orig_post = requests.post
        requests.post = fake_post
        try:
            result = cloud.transcribe(audio, 16000, cfg)
        finally:
            requests.post = orig_post

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

        orig_post = requests.post
        requests.post = fake_post
        try:
            cloud.transcribe(audio, 16000, cfg)
        finally:
            requests.post = orig_post

        self.assertNotIn("language", captured["data"])

    def test_non_200_raises_cloud_error(self):
        import numpy as np

        cfg = FakeConfig(groq_api_key="secret-key")
        audio = np.zeros(16000, dtype="float32")

        def fake_post(url, headers=None, data=None, files=None, timeout=None):
            return FakeResponse(status_code=401, text="unauthorized")

        orig_post = requests.post
        requests.post = fake_post
        try:
            with self.assertRaises(CloudError):
                cloud.transcribe(audio, 16000, cfg)
        finally:
            requests.post = orig_post

    def test_empty_response_raises_cloud_error(self):
        import numpy as np

        cfg = FakeConfig(groq_api_key="secret-key")
        audio = np.zeros(16000, dtype="float32")

        def fake_post(url, headers=None, data=None, files=None, timeout=None):
            return FakeResponse(status_code=200, text="   ")

        orig_post = requests.post
        requests.post = fake_post
        try:
            with self.assertRaises(CloudError):
                cloud.transcribe(audio, 16000, cfg)
        finally:
            requests.post = orig_post

    def test_network_error_raises_cloud_error(self):
        import numpy as np

        cfg = FakeConfig(groq_api_key="secret-key")
        audio = np.zeros(16000, dtype="float32")

        def fake_post(*args, **kwargs):
            raise requests.exceptions.ConnectionError("boom")

        orig_post = requests.post
        requests.post = fake_post
        try:
            with self.assertRaises(CloudError):
                cloud.transcribe(audio, 16000, cfg)
        finally:
            requests.post = orig_post


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

        orig_post = requests.post
        requests.post = fake_post
        try:
            result = cloud.clean("um cleaned text", cfg)
        finally:
            requests.post = orig_post

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

        orig_post = requests.post
        requests.post = fake_post
        try:
            with self.assertRaises(CloudError):
                cloud.clean("some text", cfg)
        finally:
            requests.post = orig_post

    def test_sanity_check_failure_raises_cloud_error(self):
        cfg = FakeConfig(groq_api_key="secret-key")

        def fake_post(url, headers=None, json=None, timeout=None):
            # Absurdly short rewrite of a long input should fail the shared
            # sanity check reused from flowlocal.cleaner.
            return FakeResponse(
                status_code=200,
                json_data={"choices": [{"message": {"content": "x"}}]},
            )

        orig_post = requests.post
        requests.post = fake_post
        try:
            with self.assertRaises(CloudError):
                cloud.clean("this is a reasonably long piece of dictated text", cfg)
        finally:
            requests.post = orig_post


class TestCheck(unittest.TestCase):
    def test_builds_correct_request(self):
        cfg = FakeConfig(groq_api_key="secret-key")

        captured = {}

        def fake_get(url, headers=None, timeout=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["timeout"] = timeout
            return FakeResponse(status_code=200)

        orig_get = requests.get
        requests.get = fake_get
        try:
            ok, msg = cloud.check(cfg)
        finally:
            requests.get = orig_get

        self.assertTrue(ok)
        self.assertEqual(msg, "Connected")
        self.assertEqual(captured["url"], cloud._GROQ_MODELS_URL)
        self.assertEqual(captured["headers"]["Authorization"], "Bearer secret-key")
        self.assertEqual(captured["timeout"], 10)

    def test_non_200_returns_false(self):
        cfg = FakeConfig(groq_api_key="secret-key")

        def fake_get(url, headers=None, timeout=None):
            return FakeResponse(status_code=401)

        orig_get = requests.get
        requests.get = fake_get
        try:
            ok, msg = cloud.check(cfg)
        finally:
            requests.get = orig_get

        self.assertFalse(ok)
        self.assertTrue(msg)

    def test_network_error_returns_false(self):
        cfg = FakeConfig(groq_api_key="secret-key")

        def fake_get(*args, **kwargs):
            raise requests.exceptions.ConnectionError("boom")

        orig_get = requests.get
        requests.get = fake_get
        try:
            ok, msg = cloud.check(cfg)
        finally:
            requests.get = orig_get

        self.assertFalse(ok)
        self.assertTrue(msg)


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
