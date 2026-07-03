"""App wiring: single-instance guard, state machine, pipeline worker.

THREADING MODEL (see also flowlocal/ui/window.py docstring):
- The Tk root (hidden/withdrawn) runs `mainloop()` on the MAIN thread for
  the lifetime of the process. This is required because Tcl/Tk is not
  thread-safe.
- The tray icon (pystray) runs detached on its own background thread via
  `Tray.run_detached()`.
- pynput's keyboard/mouse listeners run on their own daemon threads
  (managed internally by TriggerManager). For suppressed bindings (default
  mouse:x2, single keys), pynput's win32_event_filter calls
  App._on_trigger_press/_on_trigger_release SYNCHRONOUSLY inside the
  Windows low-level hook callback. Windows enforces a ~300ms budget on
  that callback (LowLevelHooksTimeout) — blow through it and the OS
  silently removes the hook, permanently killing the trigger until
  restart. So _on_trigger_press/_on_trigger_release/_on_trigger_cancel do
  ONLY cheap, synchronous state-machine work (see flowlocal/session_state.py)
  and immediately hand off anything slow — get_app_context() (cross-process
  win32), recorder.start()/stop() (PortAudio stream open/close, tens to
  hundreds of ms), sound cues, tray/overlay updates — to a short-lived
  daemon thread.
- The dictation pipeline (record -> transcribe -> clean -> inject) runs on
  a single dedicated worker thread with a queue of depth 1: a trigger
  press while a recording/transcription is already in progress is rejected
  (error cue) rather than queued, so dictations never interleave.
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
from flowlocal import cloud as cloud_mod
from flowlocal import context as context_mod
from flowlocal import focus
from flowlocal import history as history_mod
from flowlocal import injector
from flowlocal import overlay
from flowlocal import tray as tray_mod
from flowlocal import ui as app_ui
from flowlocal import autostart
from flowlocal import hotkey as hotkey_mod
from flowlocal.recorder import Recorder
from flowlocal.session_state import SessionState
from flowlocal.transcriber import Transcriber

logger = logging.getLogger(__name__)

_MUTEX_NAME = "Global\\FlowLocal_SingleInstance"

# Recent-dictation continuity window: a previous dictation's tail is only
# offered as context (prompt "previous" + Whisper initial_prompt bias, and
# eligibility for the undo voice command) when it landed less than this many
# seconds ago.
_CONTINUITY_WINDOW_SECONDS = 120.0
_PREVIOUS_CONTEXT_CHARS = 200
_PREVIOUS_INITIAL_PROMPT_CHARS = 100
_VOCABULARY_PROMPT_MAX_CHARS = 200
# Cap on the previous-dictation tail forwarded into the stage-2 cleanup
# prompt (via _clean -> build_rewrite_prompt). Explicit and independent of
# _PREVIOUS_CONTEXT_CHARS (the storage cap applied when _last_injected_text
# is set) so the cleanup-prompt budget doesn't implicitly ride on that
# unrelated cap.
_PREVIOUS_CLEANUP_PROMPT_CHARS = 200


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
        self.recorder.on_level = overlay.set_level_threadsafe
        self.recorder.on_auto_stop = self._on_auto_stop
        self.transcriber = Transcriber(self.cfg.model)
        self.transcriber.on_status = self._on_transcriber_status
        self.tray = tray_mod.Tray(
            on_open=self._open_home,
            on_settings=self._open_settings,
            on_toggle_pause=self._toggle_pause,
            on_quit=self.quit,
        )
        self.trigger_manager = hotkey_mod.TriggerManager(
            binding=self.cfg.trigger,
            mode=self.cfg.mode,
            on_press=self._on_trigger_press,
            on_release=self._on_trigger_release,
            on_cancel=self._on_trigger_cancel,
        )

        self._paused = False
        self._session = SessionState()
        self._work_queue: "queue.Queue" = queue.Queue()
        self._worker_thread: Optional[threading.Thread] = None
        self._running = False

        # Recent-dictation continuity (feature 4) + app-aware context
        # (feature 3) state. `_pending_app_context` is captured once at
        # trigger press (before the user can alt-tab away) and carried
        # through the queue item to `_process`/`_clean`.
        self._last_injected_text: Optional[str] = None
        self._last_injected_at: Optional[float] = None
        self._pending_app_context: Optional[str] = None

        self._tk_root = None

    # --- lifecycle ----------------------------------------------------

    def run(self, open_window_on_start: bool = False) -> None:
        """Blocking entrypoint. Runs the Tk mainloop on this (main) thread
        after starting the tray, listeners, and worker on background
        threads.

        `open_window_on_start` opens the app window (home page) right
        away — used by `python -m flowlocal --window` for screenshot
        verification without needing to click the tray icon.
        """
        import tkinter as tk

        self._running = True

        self._tk_root = tk.Tk()
        self._tk_root.withdraw()
        overlay.start_poller(self._tk_root, enabled=self.cfg.show_overlay)

        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()

        self.trigger_manager.start()
        self.tray.run_detached()
        threading.Thread(target=self._warmup, daemon=True).start()

        if open_window_on_start:
            self._tk_root.after(0, self._open_home)

        try:
            self._tk_root.mainloop()
        finally:
            self._teardown()

    def _warmup(self) -> None:
        """Pre-load the STT model (and JIT CUDA kernels) plus the Ollama
        model so the first real dictation responds at full speed. Skipped
        entirely when backend == "cloud": the GPU should stay idle and the
        cloud API needs no warmup.
        """
        if self.cfg.backend == "cloud":
            logger.info("Backend active: cloud (Groq) — skipping local model warmup")
        else:
            logger.info("Backend active: local")
            self.transcriber.warmup()
            cleaner.warmup(self.cfg)
        # Pay the one-time comtypes codegen cost (~0.5s) now, on this
        # warmup thread, without creating/caching a COM object here — UIA
        # COM objects are per-thread and must not be created on this thread
        # then used from the worker thread. The worker thread lazily
        # creates its own UIA object on first real dictation.
        try:
            focus.prewarm()
        except Exception:
            pass

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
        try:
            self.recorder.stop()
        except Exception:
            pass
        self._work_queue.put(None)

    # --- tray callbacks -------------------------------------------------

    def _build_ui_deps(self) -> dict:
        return {
            "list_devices": self._list_devices_safe,
            "refresh_devices": self._refresh_devices_safe,
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
            "on_backend_change": self._on_backend_change,
            "on_groq_api_key_change": self._on_groq_api_key_change,
            "on_cloud_stt_model_change": self._on_cloud_stt_model_change,
            "on_cloud_llm_model_change": self._on_cloud_llm_model_change,
            "on_vocabulary_change": self._on_vocabulary_change,
            "on_smart_context_change": self._on_smart_context_change,
            "on_voice_commands_change": self._on_voice_commands_change,
            "on_theme_change": self._on_theme_change,
            "on_save_history_change": self._on_save_history_change,
            "ollama_available": cleaner.ollama_available,
            "groq_check": cloud_mod.check,
            "capture_next": self.trigger_manager.capture_next,
            "cancel_capture": self.trigger_manager.cancel_capture,
            "is_paused": lambda: self._paused,
            "history": history_mod,
        }

    def _open_window(self, page: str) -> None:
        if self._tk_root is None:
            return

        def _do_open():
            deps = self._build_ui_deps()
            app_ui.open_window(self._tk_root, self.cfg, deps, page=page)

        self._tk_root.after(0, _do_open)

    def _open_home(self) -> None:
        self._open_window("home")

    def _open_settings(self) -> None:
        self._open_window("settings")

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

    def _refresh_devices_safe(self) -> None:
        try:
            self.recorder.refresh_devices()
        except Exception as exc:
            logger.warning("Could not refresh input devices: %s", exc)

    def _on_transcriber_status(self, msg: str) -> None:
        try:
            self.tray.notify(msg)
        except Exception:
            pass

    # --- settings live-apply callbacks -----------------------------------

    def _on_mic_change(self, index, name=None) -> None:
        self.cfg.mic_device = index
        self.cfg.mic_device_name = name

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
        overlay.set_enabled_threadsafe(value)

    def _on_backend_change(self, backend: str) -> None:
        self.cfg.backend = backend

        if backend == "cloud":
            def _release():
                try:
                    self.transcriber.release()
                except Exception as exc:
                    logger.warning("Failed to release local transcriber: %s", exc)
                try:
                    cleaner.unload(self.cfg)
                except Exception as exc:
                    logger.debug("Failed to unload Ollama model: %s", exc)

            logger.info("Backend switched to cloud — freeing local GPU resources")
            threading.Thread(target=_release, daemon=True).start()
        else:
            logger.info("Backend switched to local — warming up local models")
            threading.Thread(target=self._warmup, daemon=True).start()

    def _on_groq_api_key_change(self, value: str) -> None:
        self.cfg.groq_api_key = value

    def _on_cloud_stt_model_change(self, value: str) -> None:
        self.cfg.cloud_stt_model = value

    def _on_cloud_llm_model_change(self, value: str) -> None:
        self.cfg.cloud_llm_model = value

    def _on_vocabulary_change(self, value: list) -> None:
        self.cfg.vocabulary = value

    def _on_smart_context_change(self, value: bool) -> None:
        self.cfg.smart_context = value

    def _on_voice_commands_change(self, value: bool) -> None:
        self.cfg.voice_commands = value

    def _on_theme_change(self, value: str) -> None:
        self.cfg.theme = value
        try:
            self.cfg.save()
        except Exception as exc:
            logger.warning("Failed to persist theme change: %s", exc)

    def _on_save_history_change(self, value: bool) -> None:
        self.cfg.save_history = value

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
    #
    # _on_trigger_press/_on_trigger_release/_on_trigger_cancel run
    # SYNCHRONOUSLY on the Windows low-level hook thread for suppressed
    # bindings (see module docstring) and must return in well under the
    # ~300ms LowLevelHooksTimeout. They therefore only touch
    # `self._session` (a lock-guarded pure state machine, see
    # flowlocal/session_state.py) and spawn daemon threads for everything
    # else. `_session.claim_finish()`/`.press()` are the single source of
    # truth for "who gets to stop/start the recorder" — release, Esc-cancel,
    # and auto-stop race each other through the same atomic claim so
    # exactly one of them ever calls recorder.stop().

    def _on_trigger_press(self) -> bool:
        """Attempt to start a recording. Returns True if the press was
        accepted (recording start was kicked off), False if rejected
        (paused or already busy). Hold mode ignores the return value;
        toggle mode uses it to decide whether to flip into the "recording"
        state (see TriggerManager._fire_press). The actual mic open and app
        context capture happen on a background thread — this method itself
        does only the accept/reject decision plus a lock-guarded state flip.
        """
        if self._paused:
            return False

        if not self._session.press():
            logger.warning("Trigger pressed while pipeline busy; rejecting")
            sounds.play_error(self.cfg)
            self.tray.notify("Still processing previous dictation")
            return False

        threading.Thread(target=self._start_recording_worker, daemon=True).start()
        return True

    def _start_recording_worker(self) -> None:
        """Runs on a short-lived daemon thread after a press is accepted:
        captures app context, opens the mic (recorder.start(), which can
        take 50-300ms plus a device-enumeration cost on a cold cache), and
        fires the start cue/state updates. None of this may run on the hook
        thread (see module docstring).
        """
        # Capture the foreground app NOW, before the user can alt-tab
        # elsewhere while speaking. None on any failure or when disabled.
        pending_app_context = None
        if getattr(self.cfg, "smart_context", True):
            try:
                pending_app_context = context_mod.get_app_context()
            except Exception as exc:
                logger.debug("Failed to capture app context: %s", exc)
                pending_app_context = None
        self._pending_app_context = pending_app_context

        try:
            self.recorder.start(
                device_index=self.cfg.mic_device,
                max_seconds=self.cfg.max_record_seconds,
                device_name=self.cfg.mic_device_name,
            )
        except Exception as exc:
            logger.error("Failed to start recording: %s", exc)
            sounds.play_error(self.cfg)
            self.tray.notify(f"Microphone error: {exc}")
            self._session.start_failed()
            self.tray.set_state(tray_mod.STATE_IDLE)
            overlay.set_state_threadsafe(overlay.STATE_IDLE)
            return

        result = self._session.start_succeeded()
        if result.claimed:
            # A release/cancel arrived while the mic was still opening (a
            # very quick tap): honor it immediately rather than settling
            # into RECORDING state first, so the state machine never gets
            # wedged waiting for a release that already happened.
            logger.debug("Trigger released/cancelled during recorder startup; finishing immediately")
            self._run_finish(cancel=result.cancel)
            return

        sounds.play_start(self.cfg)
        self.tray.set_state(tray_mod.STATE_RECORDING)
        overlay.set_state_threadsafe(overlay.STATE_RECORDING)

    def _on_trigger_release(self) -> None:
        result = self._session.claim_finish(cancel=False)
        if result.claimed:
            threading.Thread(target=self._run_finish, kwargs={"cancel": False}, daemon=True).start()
        # If result.pending is True, the starter thread (still opening the
        # mic) will see the pending stop and finish as soon as recorder.start
        # completes — nothing more to do here. If neither claimed nor
        # pending, this release doesn't correspond to an in-progress
        # recording (e.g. it followed a rejected press) — no-op.

    def _on_trigger_cancel(self) -> None:
        """Esc was pressed while a recording was in progress (or still
        starting): stop the recorder and discard the captured audio (no
        enqueue) rather than transcribing it.
        """
        result = self._session.claim_finish(cancel=True)
        if result.claimed:
            threading.Thread(target=self._run_finish, kwargs={"cancel": True}, daemon=True).start()
        # Pending case: the starter thread will discard once recorder.start
        # completes, same as above.

    def _on_auto_stop(self) -> None:
        """Called from the recorder's audio callback (PortAudio) thread
        exactly once when max_record_seconds is hit. Stopping the stream
        must not happen synchronously from within its own callback (that
        can deadlock), so the actual finish work is dispatched onto a
        short-lived daemon thread; this method itself only does the quick
        atomic claim before returning control to the audio callback.
        """
        result = self._session.claim_finish(cancel=False)
        if not result.claimed:
            # Already claimed by a release/cancel that raced this auto-stop,
            # or nothing is actually in progress — auto-stop is a no-op.
            return

        def _run():
            self._run_finish(cancel=False)
            self.tray.notify("Max recording length reached — transcribing")

        threading.Thread(target=_run, daemon=True).start()

    def _run_finish(self, cancel: bool) -> None:
        """Stop the recorder and either enqueue the captured audio for
        transcription (cancel=False) or discard it (cancel=True). Called
        exactly once per recording — the caller must already hold the
        FINISHING claim from `self._session.claim_finish()`.
        """
        try:
            audio = self.recorder.stop()
        except Exception as exc:
            logger.error("Failed to stop recording: %s", exc)
            sounds.play_error(self.cfg)
            self.tray.notify(f"Recording error: {exc}")
            self.tray.set_state(tray_mod.STATE_IDLE)
            overlay.set_state_threadsafe(overlay.STATE_IDLE)
            self._session.finished()
            return

        if cancel:
            sounds.play_error(self.cfg)
            self.tray.set_state(tray_mod.STATE_IDLE)
            overlay.set_state_threadsafe(overlay.STATE_IDLE)
            self._session.finished()
            return

        sounds.play_stop(self.cfg)
        self.tray.set_state(tray_mod.STATE_TRANSCRIBING)
        overlay.set_state_threadsafe(overlay.STATE_TRANSCRIBING)
        # Carry the app context captured at trigger-press time through the
        # queue alongside the audio (a plain dict, not the None sentinel
        # used for shutdown). The worker thread transitions the session
        # back to IDLE once processing completes (see _worker_loop).
        self._work_queue.put({"audio": audio, "app_context": self._pending_app_context})

    # --- worker thread ----------------------------------------------------

    def _worker_loop(self) -> None:
        while True:
            item = self._work_queue.get()
            if item is None:
                return
            try:
                self._process(item["audio"], item.get("app_context"))
            except Exception:
                logger.error("Unhandled pipeline error:\n%s", traceback.format_exc())
                sounds.play_error(self.cfg)
                self.tray.notify("Dictation failed — see log for details")
            finally:
                self.tray.set_state(tray_mod.STATE_IDLE)
                overlay.set_state_threadsafe(overlay.STATE_IDLE)
                self._session.finished()

    def _recent_previous_text(self) -> Optional[str]:
        """Return the tail of the last successfully injected dictation if it
        landed less than _CONTINUITY_WINDOW_SECONDS ago, else None.
        """
        import time

        if self._last_injected_text is None or self._last_injected_at is None:
            return None
        if time.monotonic() - self._last_injected_at >= _CONTINUITY_WINDOW_SECONDS:
            return None
        return self._last_injected_text

    def _build_initial_prompt(self, previous_text: Optional[str]) -> Optional[str]:
        """Build the Whisper initial_prompt: glossary (from cfg.vocabulary,
        capped) plus a trimmed tail of the previous dictation for
        continuity. Returns None when there is nothing to bias with.
        """
        parts = []

        vocabulary = getattr(self.cfg, "vocabulary", None)
        if vocabulary:
            glossary = "Glossary: " + ", ".join(vocabulary) + "."
            if len(glossary) > _VOCABULARY_PROMPT_MAX_CHARS:
                glossary = glossary[:_VOCABULARY_PROMPT_MAX_CHARS]
            parts.append(glossary)

        if previous_text:
            parts.append(previous_text[-_PREVIOUS_INITIAL_PROMPT_CHARS:])

        if not parts:
            return None
        return " ".join(parts)

    def _clean(self, raw_text: str, app_context=None, previous=None) -> str:
        """Stage-1 rules always run locally. Stage-2 LLM rewrite (when
        cfg.clean_llm) goes to Groq on the cloud backend or Ollama on the
        local backend. A cloud stage-2 failure keeps the stage-1 text
        rather than falling back to Ollama (that would defeat the point of
        cloud mode — keeping the GPU idle).
        """
        stage1_result = cleaner._stage1_rules(raw_text, self.cfg)
        if not self.cfg.clean_llm or not stage1_result:
            return stage1_result

        # Trim explicitly here rather than relying on the caller (or the
        # unrelated _last_injected_text storage cap) to have already capped
        # this — keeps the cleanup-prompt budget self-contained (FIX 6a).
        if previous:
            previous = previous[-_PREVIOUS_CLEANUP_PROMPT_CHARS:]

        if self.cfg.backend == "cloud":
            try:
                return cloud_mod.clean(raw_text, self.cfg, app_context=app_context, previous=previous)
            except cloud_mod.CloudError as exc:
                logger.warning("Cloud cleanup failed, keeping stage-1 text: %s", exc)
                return stage1_result

        return cleaner.clean(raw_text, self.cfg, app_context=app_context, previous=previous)

    def _process(self, audio, app_context=None) -> None:
        import time

        audio_seconds = len(audio) / 16000.0 if audio is not None else 0.0

        previous_text = self._recent_previous_text()
        initial_prompt = self._build_initial_prompt(previous_text)

        # Re-read cfg.backend right here, immediately before routing (FIX 5,
        # H1/L1) rather than trusting a value captured earlier/elsewhere: an
        # item queued before a mid-flight backend switch must follow the
        # NEW backend, not whatever was configured when it was recorded —
        # the whole point of switching to cloud is to keep the GPU idle, so
        # an item silently falling back to loading the local model right
        # after the user switched away from it would defeat that. This is
        # also the guard for the local branch: the only sanctioned way
        # local ever loads while cfg.backend == "cloud" is the CloudError
        # fallback below (intentional — keeps dictation working if Groq is
        # briefly down), never a stale local route.
        stt_start = time.monotonic()
        if self.cfg.backend == "cloud":
            try:
                raw_text = cloud_mod.transcribe(audio, 16000, self.cfg, prompt=initial_prompt)
            except cloud_mod.CloudError as exc:
                logger.warning("Cloud transcription failed (%s); falling back to local", exc)
                self.tray.notify(f"Cloud transcription failed ({exc}) — using local model")
                try:
                    raw_text = self.transcriber.transcribe(
                        audio, language=self.cfg.language, initial_prompt=initial_prompt
                    )
                except Exception as exc2:
                    logger.error("Local fallback transcription failed: %s", exc2)
                    sounds.play_error(self.cfg)
                    self.tray.notify(f"Transcription failed: {exc2}")
                    return
        else:
            try:
                raw_text = self.transcriber.transcribe(
                    audio, language=self.cfg.language, initial_prompt=initial_prompt
                )
            except Exception as exc:
                logger.error("Transcription failed: %s", exc)
                sounds.play_error(self.cfg)
                self.tray.notify(f"Transcription failed: {exc}")
                return
        stt_elapsed = time.monotonic() - stt_start

        if not raw_text:
            return  # silence/empty: skip silently

        # Voice command: whole-utterance undo ("scratch that", etc.), only
        # honored when a previous injection happened recently. Checked
        # BEFORE the hallucination guard (FIX 6b) — the two phrase sets are
        # disjoint (see tests.test_cleaner.test_undo_and_hallucination_
        # phrases_are_disjoint) but ordering undo first keeps the intent
        # explicit regardless.
        if getattr(self.cfg, "voice_commands", True) and cleaner.is_undo_command(raw_text):
            if (
                self._last_injected_at is not None
                and time.monotonic() - self._last_injected_at < _CONTINUITY_WINDOW_SECONDS
            ):
                try:
                    injector.send_undo()
                    self.tray.notify("Undone")
                except Exception as exc:
                    logger.warning("Undo injection failed: %s", exc)
                    sounds.play_error(self.cfg)
                self._last_injected_text = None
                self._last_injected_at = None
            else:
                logger.info("Ignoring undo command; no recent injection")
            return

        if cleaner.is_hallucination(raw_text):
            logger.debug("Discarding likely Whisper hallucination (%d chars)", len(raw_text))
            return  # treat as empty, same as the silence path

        clean_start = time.monotonic()
        clean_text = self._clean(raw_text, app_context=app_context, previous=previous_text)
        clean_elapsed = time.monotonic() - clean_start
        if not clean_text:
            return

        # Inject FIRST (FIX 4): the focus probe is a 20-120ms UIA COM
        # round-trip that only feeds the overlay's landed-in-textbox
        # animation choice — it must not delay the paste itself. Run it
        # after a successful inject so it also reflects where the text
        # actually landed rather than where focus was a moment earlier.
        inject_start = time.monotonic()
        try:
            injector.inject(clean_text)
        except injector.InjectionFallback as exc:
            logger.warning("Injection failed: %s", exc)
            sounds.play_error(self.cfg)
            if exc.text_on_clipboard:
                self.tray.notify("Paste failed — text is on your clipboard")
            else:
                self.tray.notify(
                    "Injection failed — hover the bottom pill to copy your text"
                )
            overlay.notify_result_threadsafe(clean_text, landed_in_textbox=False)
        except Exception as exc:
            logger.error("Unexpected injection error: %s", exc)
            sounds.play_error(self.cfg)
            self.tray.notify(f"Injection error: {exc}")
        else:
            text_input_focused = focus.is_text_input_focused()
            self._last_injected_text = clean_text[-_PREVIOUS_CONTEXT_CHARS:]
            self._last_injected_at = time.monotonic()
            overlay.notify_result_threadsafe(
                clean_text, landed_in_textbox=text_input_focused
            )
        finally:
            inject_elapsed = time.monotonic() - inject_start
            logger.info(
                "dictation: %.1fs audio | stt %.2fs | clean %.2fs | inject %.2fs",
                audio_seconds, stt_elapsed, clean_elapsed, inject_elapsed,
            )

        if getattr(self.cfg, "save_history", True) and clean_text:
            try:
                history_mod.add(clean_text, seconds=audio_seconds)
            except Exception as exc:
                logger.warning("Failed to record dictation history: %s", exc)
