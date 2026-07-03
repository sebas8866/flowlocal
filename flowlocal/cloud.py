"""Optional cloud processing backend via Groq (api.groq.com).

Only ever called when cfg.backend == "cloud" and the user has opted in with
an API key. `requests` is imported lazily inside functions so this module
can be imported without it installed. All failures raise `CloudError` with
a concise human-readable message — callers decide whether/how to fall back.
"""
from __future__ import annotations

import io
import logging
import wave

logger = logging.getLogger(__name__)

_GROQ_BASE_URL = "https://api.groq.com/openai/v1"
_GROQ_TRANSCRIPTIONS_URL = f"{_GROQ_BASE_URL}/audio/transcriptions"
_GROQ_CHAT_URL = f"{_GROQ_BASE_URL}/chat/completions"
_GROQ_MODELS_URL = f"{_GROQ_BASE_URL}/models"

_SAMPLE_WIDTH_BYTES = 2  # 16-bit PCM


class CloudError(Exception):
    """Raised on any failure talking to the Groq API (missing key, network
    error, non-200 response, empty/invalid response)."""


def _require_key(cfg) -> str:
    key = getattr(cfg, "groq_api_key", "") or ""
    if not key.strip():
        raise CloudError("No Groq API key configured")
    return key.strip()


def _encode_wav(audio, samplerate: int) -> bytes:
    """Convert a float32 [-1, 1] mono numpy array to an in-memory 16-bit
    PCM WAV file (stdlib `wave` + `io.BytesIO`, no temp files).
    """
    import numpy as np

    clipped = np.clip(audio, -1.0, 1.0)
    pcm = (clipped * 32767.0).astype("<i2")

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(_SAMPLE_WIDTH_BYTES)
        wf.setframerate(samplerate)
        wf.writeframes(pcm.tobytes())
    buf.seek(0)
    return buf.getvalue()


def transcribe(audio, samplerate: int, cfg, prompt: str = None) -> str:
    """POST audio to Groq's Whisper endpoint; return the transcript text.

    `prompt`, when given, is forwarded as the Groq transcription endpoint's
    `prompt` form field (recent-dictation continuity / vocabulary bias).

    Raises CloudError on any failure. Callers should fall back to the local
    transcriber.
    """
    key = _require_key(cfg)

    try:
        wav_bytes = _encode_wav(audio, samplerate)
    except Exception as exc:
        raise CloudError(f"Failed to encode audio: {exc}") from exc

    model = getattr(cfg, "cloud_stt_model", "whisper-large-v3-turbo") or "whisper-large-v3-turbo"
    language = getattr(cfg, "language", None)

    data = {"model": model, "response_format": "text"}
    if language:
        data["language"] = language
    if prompt:
        data["prompt"] = prompt

    try:
        import requests

        resp = requests.post(
            _GROQ_TRANSCRIPTIONS_URL,
            headers={"Authorization": f"Bearer {key}"},
            data=data,
            files={"file": ("audio.wav", io.BytesIO(wav_bytes), "audio/wav")},
            timeout=20,
        )
    except Exception as exc:
        raise CloudError(f"Network error: {exc}") from exc

    if resp.status_code != 200:
        raise CloudError(f"Groq STT returned HTTP {resp.status_code}")

    text = (resp.text or "").strip()
    if not text:
        raise CloudError("Groq STT returned an empty transcript")

    return text


def clean(text: str, cfg, app_context=None, previous=None) -> str:
    """POST the transcript to Groq's chat completions endpoint for a
    grammar/false-start rewrite, reusing cleaner.build_rewrite_prompt and
    cleaner.sanity_check so both LLM paths share the same prompt and output
    guard. Raises CloudError on any failure or implausible output.
    """
    from flowlocal import cleaner

    key = _require_key(cfg)

    model = getattr(cfg, "cloud_llm_model", "llama-3.3-70b-versatile") or "llama-3.3-70b-versatile"
    prompt = cleaner.build_rewrite_prompt(text, cfg, app_context=app_context, previous=previous)

    payload = {
        "model": model,
        "temperature": 0.2,
        "messages": [{"role": "user", "content": prompt}],
    }

    try:
        import requests

        resp = requests.post(
            _GROQ_CHAT_URL,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=20,
        )
    except Exception as exc:
        raise CloudError(f"Network error: {exc}") from exc

    if resp.status_code != 200:
        raise CloudError(f"Groq LLM returned HTTP {resp.status_code}")

    try:
        body = resp.json()
        rewritten = (body["choices"][0]["message"]["content"] or "").strip()
    except Exception as exc:
        raise CloudError(f"Malformed Groq LLM response: {exc}") from exc

    if not cleaner.sanity_check(text, rewritten):
        raise CloudError("Groq LLM output failed sanity check")

    return rewritten


def check(cfg) -> tuple:
    """Cheap validity probe for the settings UI: GET /models with the key.

    Returns (True, "Connected") on success, (False, short reason) otherwise.
    Never raises.
    """
    key = (getattr(cfg, "groq_api_key", "") or "").strip()
    if not key:
        return False, "No API key set"

    try:
        import requests

        resp = requests.get(
            _GROQ_MODELS_URL,
            headers={"Authorization": f"Bearer {key}"},
            timeout=10,
        )
    except Exception as exc:
        return False, f"Network error: {exc}"

    if resp.status_code == 200:
        return True, "Connected"
    if resp.status_code == 401:
        return False, "Invalid API key"
    return False, f"HTTP {resp.status_code}"
