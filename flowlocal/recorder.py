"""Microphone capture via sounddevice.

`sounddevice`/`numpy` are imported lazily inside methods so importing this
module never fails on an interpreter without them installed.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = "float32"

# Soft-scaling multiplier applied to per-block RMS to derive a 0..1 loudness
# level for the overlay's live waveform. Tuneable.
_LEVEL_SCALE = 12.0


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
        # Optional callback invoked from the audio thread with a float in
        # [0, 1] representing the current block's loudness. Set directly
        # (e.g. `recorder.on_level = fn`) or pass to future constructors.
        self.on_level: Optional[Callable[[float], None]] = None
        # Optional callback invoked exactly once from the audio callback
        # thread when max_record_seconds is hit. Exception-guarded like
        # on_level so a broken handler never breaks audio capture.
        self.on_auto_stop: Optional[Callable[[], None]] = None
        self._auto_stop_fired = False

        # Cache of the last resolved (device_name -> device_index) lookup so
        # start() doesn't have to call list_devices() (a full
        # sd.query_devices() enumeration) on every single trigger press.
        # Invalidated whenever the device set changes (refresh_devices())
        # or the caller resolves a different name.
        self._resolved_device_name: Optional[str] = None
        self._resolved_device_index: Optional[int] = None

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def auto_stopped(self) -> bool:
        """True if the last recording was cut short by max_record_seconds."""
        return self._auto_stopped

    def start(
        self,
        device_index: Optional[int] = None,
        max_seconds: Optional[int] = None,
        device_name: Optional[str] = None,
    ) -> None:
        """Open an InputStream and begin accumulating audio blocks.

        Device resolution order: if `device_name` is given, look up the
        *current* device index by exact name match (device indices can
        shift across reboots/USB replug, names are stable); fall back to
        `device_index` if the name isn't found, then to the system default
        input device (None) if neither resolves.
        """
        import sounddevice as sd

        if self._recording:
            logger.warning("Recorder.start called while already recording")
            return

        if device_name:
            if (
                self._resolved_device_name == device_name
                and self._resolved_device_index is not None
            ):
                device_index = self._resolved_device_index
            else:
                resolved_index = None
                try:
                    for idx, name in self.list_devices():
                        if name == device_name:
                            resolved_index = idx
                            break
                except Exception as exc:
                    logger.warning("Could not resolve mic device by name: %s", exc)
                if resolved_index is not None:
                    device_index = resolved_index
                    self._resolved_device_name = device_name
                    self._resolved_device_index = resolved_index
                else:
                    logger.warning(
                        "Mic device name %r not found; falling back to stored index/default",
                        device_name,
                    )
                    # Don't cache a miss: keep retrying resolution on
                    # subsequent presses in case the device reappears.
                    self._resolved_device_name = None
                    self._resolved_device_index = None

        self._blocks = []
        self._recording = True
        self._auto_stopped = False
        self._auto_stop_fired = False
        self._max_seconds = max_seconds
        self._start_time = time.monotonic()

        def _callback(indata, frames, time_info, status):
            import numpy as np

            if status:
                logger.debug("Recorder stream status: %s", status)
            with self._lock:
                if self._recording:
                    self._blocks.append(indata.copy())
            if self._max_seconds is not None and self._start_time is not None:
                elapsed = time.monotonic() - self._start_time
                if elapsed >= self._max_seconds:
                    self._auto_stopped = True
                    if not self._auto_stop_fired:
                        self._auto_stop_fired = True
                        if self.on_auto_stop is not None:
                            try:
                                self.on_auto_stop()
                            except Exception:
                                # A broken callback must never break audio capture.
                                pass

            if self.on_level is not None:
                try:
                    rms = float(np.sqrt(np.mean(np.square(indata), dtype="float64")))
                    level = min(1.0, rms * _LEVEL_SCALE)
                    self.on_level(level)
                except Exception:
                    # A slow/broken callback must never break audio capture.
                    pass

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

    def refresh_devices(self) -> None:
        """Re-initialize PortAudio so newly plugged-in/removed input
        devices are picked up without restarting the app. Locked no-op
        while a recording is in progress (tearing down PortAudio mid-stream
        would break the active capture) — checked and skipped atomically
        under `_lock` so a refresh can't interleave with a start() that is
        in the middle of flipping `_recording` on.

        Invalidates the resolved-device-name cache, since device indices
        can shift after a PortAudio re-init.
        """
        with self._lock:
            if self._recording:
                logger.debug("refresh_devices skipped: recording in progress")
                return

            import sounddevice as sd

            try:
                sd._terminate()
                sd._initialize()
            except Exception as exc:
                logger.warning("Failed to refresh audio devices: %s", exc)

            self._resolved_device_name = None
            self._resolved_device_index = None
