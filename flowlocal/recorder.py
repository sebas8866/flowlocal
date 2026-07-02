"""Microphone capture via sounddevice.

`sounddevice`/`numpy` are imported lazily inside methods so importing this
module never fails on an interpreter without them installed.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = "float32"


class Recorder:
    """Captures mono 16 kHz float32 audio from a selected input device."""

    def __init__(self) -> None:
        self._stream = None
        self._blocks: List = []
        self._lock = threading.Lock()
        self._recording = False
        self._max_seconds: Optional[int] = None
        self._start_time: Optional[float] = None
        self._auto_stopped = False

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def auto_stopped(self) -> bool:
        """True if the last recording was cut short by max_record_seconds."""
        return self._auto_stopped

    def start(self, device_index: Optional[int] = None, max_seconds: Optional[int] = None) -> None:
        """Open an InputStream and begin accumulating audio blocks.

        `device_index` of None uses the system default input device.
        """
        import sounddevice as sd

        if self._recording:
            logger.warning("Recorder.start called while already recording")
            return

        self._blocks = []
        self._recording = True
        self._auto_stopped = False
        self._max_seconds = max_seconds
        self._start_time = time.monotonic()

        def _callback(indata, frames, time_info, status):
            if status:
                logger.debug("Recorder stream status: %s", status)
            with self._lock:
                if self._recording:
                    self._blocks.append(indata.copy())
            if self._max_seconds is not None and self._start_time is not None:
                elapsed = time.monotonic() - self._start_time
                if elapsed >= self._max_seconds:
                    self._auto_stopped = True

        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype=DTYPE,
            device=device_index,
            callback=_callback,
        )
        self._stream.start()

    def stop(self):
        """Stop the stream and return the concatenated audio as a numpy
        array (empty array if nothing was captured).
        """
        import numpy as np

        if not self._recording:
            if self._blocks:
                with self._lock:
                    blocks = list(self._blocks)
                if blocks:
                    return np.concatenate(blocks, axis=0).flatten()
            return np.array([], dtype="float32")

        self._recording = False

        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as exc:
                logger.warning("Error closing input stream: %s", exc)
            self._stream = None

        with self._lock:
            blocks = list(self._blocks)
            self._blocks = []

        if not blocks:
            return np.array([], dtype="float32")

        return np.concatenate(blocks, axis=0).flatten()

    @staticmethod
    def list_devices() -> List[Tuple[int, str]]:
        """Return [(index, name), ...] of input-capable devices, deduped by
        name.
        """
        import sounddevice as sd

        devices = sd.query_devices()
        seen_names = set()
        result: List[Tuple[int, str]] = []
        for index, device in enumerate(devices):
            if device.get("max_input_channels", 0) > 0:
                name = device.get("name", f"Device {index}")
                if name in seen_names:
                    continue
                seen_names.add(name)
                result.append((index, name))
        return result
