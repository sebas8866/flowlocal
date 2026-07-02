"""App wiring: single-instance guard, state machine, pipeline worker.

THREADING MODEL (see also flowlocal/settings_ui.py docstring):
- The Tk root (hidden/withdrawn) runs `mainloop()` on the MAIN thread for
  the lifetime of the process. This is required because Tcl/Tk is not
  thread-safe.
- The tray icon (pystray) runs detached on its own background thread via
  `Tray.run_detached()`.
- pynput's keyboard/mouse listeners run on their own daemon threads
  (managed internally by TriggerManager).
- The dictation pipeline (record -> transcribe -> clean -> inject) runs on
  a single dedicated worker thread with a queue of depth 1: a trigger
  press while the worker is busy is rejected (error cue) rather than
  queued, so dictations never interleave.
- Any callback that needs to touch tkinter widgets (e.g. opening Settings
  from the tray menu, which fires on the tray's thread) is marshalled onto
  the main thread via `root.after(0, ...)`.
"""
from __future__ import annotations

import logging
import queue
import threading
import traceback
from typing import Optional

from flowlocal import config as config_mod
from flowlocal import sounds
from flowlocal import cleaner
from flowlocal import injector
from flowlocal import overlay
from flowlocal import tray as tray_mod
from flowlocal import settings_ui
from flowlocal import autostart
from flowlocal import hotkey as hotkey_mod
from flowlocal.recorder import Recorder
from flowlocal.transcriber import Transcriber

logger = logging.getLogger(__name__)

_MUTEX_NAME = "Global\\FlowLocal_SingleInstance"


class SingleInstanceGuard:
    """Named-mutex guard; `acquire()` returns False if another instance
    already holds the mutex.
    """

    def __init__(self) -> None:
        self._mutex = None

    def acquire(self) -> bool:
        import win32event
        import winerror

        self._mutex = win32event.CreateMutex(None, False, _MUTEX_NAME)
        last_error = winerror.ERROR_ALREADY_EXISTS
        if __import__("win32api").GetLastError() == last_error:
            return False
        return True

    def release(self) -> None:
        if self._mutex is not None:
            try:
                import win32api

                win32api.CloseHandle(self._mutex)
            except Exception:
                pass
            self._mutex = None


class App:
    def __init__(self, cfg: Optional[config_mod.Config] = None) -> None:
        self.cfg = cfg or config_mod.Config.load()

        self.recorder = Recorder()
        self.transcriber = Transcriber(self.cfg.model)
        self.tray = tray_mod.Tray(
            on_settings=self._open_settings,
            on_toggle_pause=self._toggle_pause,
            on_quit=self.quit,
        )
        self.trigger_manager = hotkey_mod.TriggerManager(
            binding=self.cfg.trigger,
            mode=self.cfg.mode,
            on_press=self._on_trigger_press,
            on_release=self._on_trigger_release,
        )

        self._paused = False
        self._busy_lock = threading.Lock()
        self._busy = False
        self._work_queue: "queue.Queue" = queue.Queue()
        self._worker_thread: Optional[threading.Thread] = None
        self._running = False

        self._tk_root = None

    # --- lifecycle ----------------------------------------------------

    def run(self) -> None:
        """Blocking entrypoint. Runs the Tk mainloop on this (main) thread
        after starting the tray, listeners, and worker on background
        threads.
        """
        import tkinter as tk

        self._running = True

        self._tk_root = tk.Tk()
        self._tk_root.withdraw()
        overlay.start_poller(self._tk_root)

        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()

        self.trigger_manager.start()
        self.tray.run_detached()
        threading.Thread(target=self._warmup, daemon=True).start()

        try:
            self._tk_root.mainloop()
        finally:
            self._teardown()

    def _warmup(self) -> None:
        """Pre-load the STT model (and JIT CUDA kernels) plus the Ollama
        model so the first real dictation responds at full speed.
        """
        self.transcriber.warmup()
        cleaner.warmup(self.cfg)

    def quit(self) -> None:
        if self._tk_root is not None:
            try:
                self._tk_root.after(0, self._tk_root.quit)
            except Exception:
                pass

    def _teardown(self) -> None:
        self._running = False
        try:
            self.trigger_manager.stop()
        except Exception:
            pass
        try:
            self.tray.stop()
        except Exception:
            pass
        self._work_queue.put(None)

    # --- tray callbacks -------------------------------------------------

    def _open_settings(self) -> None:
        if self._tk_root is None:
            return

        def _do_open():
            deps = {
                "list_devices": self._list_devices_safe,
                "on_mic_change": self._on_mic_change,
                "on_trigger_change": self._on_trigger_change,
                "on_mode_change": self._on_mode_change,
                "on_model_change": self._on_model_change,
                "on_language_change": self._on_language_change,
                "on_clean_fillers_change": self._on_clean_fillers_change,
                "on_clean_llm_change": self._on_clean_llm_change,
                "on_sounds_change": self._on_sounds_change,
                "on_autostart_change": self._on_autostart_change,
                "on_show_overlay_change": self._on_show_overlay_change,
                "ollama_available": cleaner.ollama_available,
                "capture_next": self.trigger_manager.capture_next,
                "cancel_capture": self.trigger_manager.cancel_capture,
            }
            settings_ui.open_settings(self._tk_root, self.cfg, deps)

        self._tk_root.after(0, _do_open)

    def _toggle_pause(self) -> None:
        self._paused = not self._paused
        self.tray.set_paused(self._paused)

    @staticmethod
    def _list_devices_safe():
        try:
            return Recorder.list_devices()
        except Exception as exc:
            logger.warning("Could not list input devices: %s", exc)
            return []

    # --- settings live-apply callbacks -----------------------------------

    def _on_mic_change(self, index) -> None:
        self.cfg.mic_device = index

    def _on_trigger_change(self, binding: str) -> None:
        self.cfg.trigger = binding
        try:
            self.trigger_manager.set_binding(binding)
        except ValueError as exc:
            logger.warning("Invalid trigger binding %r: %s", binding, exc)

    def _on_mode_change(self, mode: str) -> None:
        self.cfg.mode = mode
        self.trigger_manager.set_mode(mode)

    def _on_model_change(self, model_name: str) -> None:
        self.cfg.model = model_name

        def _reload():
            try:
                self.transcriber.reload(model_name)
            except Exception as exc:
                logger.error("Failed to reload model %s: %s", model_name, exc)

        threading.Thread(target=_reload, daemon=True).start()

    def _on_language_change(self, language) -> None:
        self.cfg.language = language

    def _on_clean_fillers_change(self, value: bool) -> None:
        self.cfg.clean_fillers = value

    def _on_clean_llm_change(self, value: bool) -> None:
        self.cfg.clean_llm = value

    def _on_sounds_change(self, value: bool) -> None:
        self.cfg.sounds = value

    def _on_show_overlay_change(self, value: bool) -> None:
        self.cfg.show_overlay = value

    def _on_autostart_change(self, value: bool) -> None:
        self.cfg.autostart = value
        try:
            if value:
                autostart.enable()
            else:
                autostart.disable()
        except Exception as exc:
            logger.error("Failed to update autostart: %s", exc)

    # --- trigger -> pipeline ---------------------------------------------

    def _on_trigger_press(self) -> None:
        if self._paused:
            return

        with self._busy_lock:
            if self._busy:
                logger.warning("Trigger pressed while pipeline busy; rejecting")
                sounds.play_error(self.cfg)
                self.tray.notify("Still processing previous dictation")
                return
            self._busy = True

        try:
            self.recorder.start(
                device_index=self.cfg.mic_device,
                max_seconds=self.cfg.max_record_seconds,
            )
        except Exception as exc:
            logger.error("Failed to start recording: %s", exc)
            sounds.play_error(self.cfg)
            self.tray.notify(f"Microphone error: {exc}")
            with self._busy_lock:
                self._busy = False
            return

        sounds.play_start(self.cfg)
        self.tray.set_state(tray_mod.STATE_RECORDING)

    def _on_trigger_release(self) -> None:
        with self._busy_lock:
            if not self._busy:
                return

        try:
            audio = self.recorder.stop()
        except Exception as exc:
            logger.error("Failed to stop recording: %s", exc)
            sounds.play_error(self.cfg)
            self.tray.notify(f"Recording error: {exc}")
            self.tray.set_state(tray_mod.STATE_IDLE)
            with self._busy_lock:
                self._busy = False
            return

        sounds.play_stop(self.cfg)
        self.tray.set_state(tray_mod.STATE_TRANSCRIBING)
        self._work_queue.put(audio)

    # --- worker thread ----------------------------------------------------

    def _worker_loop(self) -> None:
        while True:
            item = self._work_queue.get()
            if item is None:
                return
            try:
                self._process(item)
            except Exception:
                logger.error("Unhandled pipeline error:\n%s", traceback.format_exc())
                sounds.play_error(self.cfg)
                self.tray.notify("Dictation failed — see log for details")
            finally:
                self.tray.set_state(tray_mod.STATE_IDLE)
                with self._busy_lock:
                    self._busy = False

    def _process(self, audio) -> None:
        import time

        audio_seconds = len(audio) / 16000.0 if audio is not None else 0.0

        stt_start = time.monotonic()
        try:
            raw_text = self.transcriber.transcribe(audio, language=self.cfg.language)
        except Exception as exc:
            logger.error("Transcription failed: %s", exc)
            sounds.play_error(self.cfg)
            self.tray.notify(f"Transcription failed: {exc}")
            return
        stt_elapsed = time.monotonic() - stt_start

        if not raw_text:
            return  # silence/empty: skip silently

        clean_start = time.monotonic()
        clean_text = cleaner.clean(raw_text, self.cfg)
        clean_elapsed = time.monotonic() - clean_start
        if not clean_text:
            return

        inject_start = time.monotonic()
        try:
            injector.inject(clean_text)
        except injector.InjectionFallback as exc:
            logger.warning("Injection failed: %s", exc)
            sounds.play_error(self.cfg)
            self.tray.notify("Paste failed — text is on your clipboard")
            if self.cfg.show_overlay:
                overlay.show_toast_threadsafe(clean_text)
        except Exception as exc:
            logger.error("Unexpected injection error: %s", exc)
            sounds.play_error(self.cfg)
            self.tray.notify(f"Injection error: {exc}")
        else:
            if self.cfg.show_overlay:
                overlay.show_toast_threadsafe(clean_text)
        finally:
            inject_elapsed = time.monotonic() - inject_start
            logger.info(
                "dictation: %.1fs audio | stt %.2fs | clean %.2fs | inject %.2fs",
                audio_seconds, stt_elapsed, clean_elapsed, inject_elapsed,
            )
