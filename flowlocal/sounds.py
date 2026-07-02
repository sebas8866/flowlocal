"""Audible feedback cues (start/stop/error) via winsound, non-blocking.

winsound is a Windows-only stdlib module; imported lazily so this module
can still be imported (and no-op) on non-Windows or headless test runs.
"""
from __future__ import annotations

import threading
import logging

logger = logging.getLogger(__name__)


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


def play_start(cfg) -> None:
    """High, short beep signalling recording has begun."""
    if not getattr(cfg, "sounds", True):
        return
    _beep_async((880, 90))


def play_stop(cfg) -> None:
    """Lower, short beep signalling recording has ended."""
    if not getattr(cfg, "sounds", True):
        return
    _beep_async((440, 90))


def play_error(cfg) -> None:
    """Low double-beep signalling an error occurred."""
    if not getattr(cfg, "sounds", True):
        return
    _beep_async((220, 120), (220, 120))
