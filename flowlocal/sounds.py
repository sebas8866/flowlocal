"""Audible feedback cues (start/stop/error), non-blocking.

Cue tones are pre-rendered to small WAV files (soft sine waves with a
smooth attack/release envelope, no clicks) cached under
%APPDATA%\\FlowLocal\\sounds\\ and played back with winsound.PlaySound in
async mode so playback never blocks recording start. winsound is a
Windows-only stdlib module; all Windows-specific imports are lazy so this
module can still be imported (and no-op) on non-Windows or headless test
runs. If WAV generation or playback fails for any reason, we fall back to
the old winsound.Beep behavior quietly.
"""
from __future__ import annotations

import logging
import math
import os
import struct
import threading
import wave

logger = logging.getLogger(__name__)

_SAMPLE_RATE = 44100
_ATTACK_MS = 10


def _sounds_dir() -> str:
    """Return %APPDATA%\\FlowLocal\\sounds, creating it if necessary."""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = os.path.join(base, "FlowLocal", "sounds")
    os.makedirs(path, exist_ok=True)
    return path


def _envelope(i: int, n: int, attack_samples: int) -> float:
    """Smooth 0->1->0 envelope: linear attack, exponential-ish release.

    Starts and ends at zero amplitude so there is no click at either edge.
    """
    if n <= 1:
        return 0.0
    if i < attack_samples:
        return i / attack_samples
    release_i = i - attack_samples
    release_n = max(1, n - attack_samples)
    # Exponential-ish decay from 1 down to 0.
    t = release_i / release_n
    return (1.0 - t) ** 2


def _render_tone(frequency: float, duration_ms: int, amplitude: float) -> bytes:
    """Render a single pure-sine tone with a soft envelope to 16-bit PCM bytes."""
    n = int(_SAMPLE_RATE * duration_ms / 1000)
    attack_samples = max(1, int(_SAMPLE_RATE * _ATTACK_MS / 1000))
    frames = bytearray()
    peak = int(amplitude * 32767)
    for i in range(n):
        env = _envelope(i, n, attack_samples)
        sample = int(peak * env * math.sin(2 * math.pi * frequency * i / _SAMPLE_RATE))
        frames += struct.pack("<h", sample)
    return bytes(frames)


def _render_sequence(tones) -> bytes:
    """Render a sequence of (frequency, duration_ms, amplitude) tones, concatenated."""
    frames = bytearray()
    for frequency, duration_ms, amplitude in tones:
        frames += _render_tone(frequency, duration_ms, amplitude)
    return bytes(frames)


def _write_wav(path: str, pcm_bytes: bytes) -> None:
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(_SAMPLE_RATE)
        wf.writeframes(pcm_bytes)


# name -> sequence of (frequency_hz, duration_ms, amplitude_0_to_1)
_CUES = {
    "start": [(523, 90, 0.20), (784, 140, 0.20)],
    "stop": [(587, 140, 0.25)],
    "error": [(330, 120, 0.30), (330, 120, 0.30)],
}


def _cue_path(name: str) -> str:
    return os.path.join(_sounds_dir(), f"{name}.wav")


def _ensure_cue(name: str) -> str:
    """Return the cached WAV path for `name`, generating it if missing."""
    path = _cue_path(name)
    if not os.path.exists(path):
        pcm_bytes = _render_sequence(_CUES[name])
        _write_wav(path, pcm_bytes)
    return path


def _beep(frequency: int, duration_ms: int) -> None:
    try:
        import winsound

        winsound.Beep(frequency, duration_ms)
    except Exception as exc:  # pragma: no cover - platform dependent
        logger.debug("Beep failed: %s", exc)


def _beep_async(*tones) -> None:
    """Play a sequence of (frequency, duration_ms) tones in a daemon thread."""

    def _run():
        for frequency, duration_ms in tones:
            _beep(frequency, duration_ms)

    threading.Thread(target=_run, daemon=True).start()


def _play_cue(name: str) -> None:
    """Play a cached cue WAV asynchronously, falling back to winsound.Beep."""

    def _run():
        try:
            import winsound

            path = _ensure_cue(name)
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception as exc:
            logger.debug("WAV cue playback failed (%s), falling back to Beep", exc)
            fallback = {
                "start": [(880, 90)],
                "stop": [(440, 90)],
                "error": [(220, 120), (220, 120)],
            }.get(name, [])
            for frequency, duration_ms in fallback:
                _beep(frequency, duration_ms)

    threading.Thread(target=_run, daemon=True).start()


def play_start(cfg) -> None:
    """Soft ascending two-note cue signalling recording has begun."""
    if not getattr(cfg, "sounds", True):
        return
    _play_cue("start")


def play_stop(cfg) -> None:
    """Soft descending single-note cue signalling recording has ended."""
    if not getattr(cfg, "sounds", True):
        return
    _play_cue("stop")


def play_error(cfg) -> None:
    """Low double-note cue signalling an error occurred."""
    if not getattr(cfg, "sounds", True):
        return
    _play_cue("error")
