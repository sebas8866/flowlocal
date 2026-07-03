"""Settings window (tkinter/ttk).

THREADING MODEL (must match flowlocal/app.py):
tkinter's Tcl interpreter is not thread-safe and must run its mainloop on
the process's MAIN thread. This app therefore runs the tk root window,
hidden (withdrawn), in the main thread's mainloop for the lifetime of the
process. pystray's tray icon runs *detached* on its own background thread
(`Tray.run_detached()`), and the pynput listeners run on their own daemon
threads. The worker/pipeline also runs on a background thread.

This module's `open_settings()` therefore does NOT create its own Tk root
or its own mainloop — it is called from the main thread (directly, or
marshalled onto the main thread via `root.after(0, ...)` if triggered from
a background thread such as the tray menu callback) and simply creates a
`Toplevel` attached to the shared hidden root. All tkinter widget creation
and mutation in this module happens on the main thread only.

Only one settings window may be open at a time; calling `open_settings`
again while one is open focuses the existing window instead of creating a
new one.
"""
from __future__ import annotations

import logging
from typing import Callable, Dict, Optional

logger = logging.getLogger(__name__)

_MODEL_PRESETS = [
    ("large-v3-turbo", "Accurate (large-v3-turbo)"),
    ("distil-large-v3", "Fast (distil-large-v3)"),
    ("small", "Light (small, CPU-friendly)"),
]

_LANGUAGES = [
    (None, "auto"),
    ("en", "English"),
    ("es", "Spanish"),
    ("fr", "French"),
    ("de", "German"),
    ("pt", "Portuguese"),
    ("it", "Italian"),
    ("nl", "Dutch"),
    ("ja", "Japanese"),
    ("ko", "Korean"),
    ("zh", "Chinese"),
    ("ru", "Russian"),
    ("hi", "Hindi"),
    ("ar", "Arabic"),
]

_BACKEND_PRESETS = [
    ("local", "Local (private, offline)"),
    ("cloud", "Cloud (Groq — fastest, needs internet)"),
]

_current_window = None  # module-level singleton guard


def _language_label_to_code(label: str) -> Optional[str]:
    for code, lbl in _LANGUAGES:
        if lbl == label:
            return code
    return None


def _language_code_to_label(code: Optional[str]) -> str:
    for c, lbl in _LANGUAGES:
        if c == code:
            return lbl
    return _LANGUAGES[0][1]


def _model_label_to_value(label: str) -> Optional[str]:
    for value, lbl in _MODEL_PRESETS:
        if lbl == label:
            return value
    return None


def _model_value_to_label(value: str) -> str:
    for v, lbl in _MODEL_PRESETS:
        if v == value:
            return lbl
    return _MODEL_PRESETS[0][1]


def _backend_label_to_value(label: str) -> str:
    for value, lbl in _BACKEND_PRESETS:
        if lbl == label:
            return value
    return _BACKEND_PRESETS[0][0]


def _backend_value_to_label(value: str) -> str:
    for v, lbl in _BACKEND_PRESETS:
        if v == value:
            return lbl
    return _BACKEND_PRESETS[0][1]


def open_settings(root, cfg, deps: Dict[str, Callable]) -> None:
    """Open (or focus) the settings window.

    `root` is the shared hidden Tk root running the main-thread mainloop.
    `cfg` is the live Config instance.
    `deps` is a dict of callbacks:
        list_devices() -> list[(index, name)]
        refresh_devices() -> None  # Recorder.refresh_devices
        on_mic_change(index_or_none, name_or_none)
        on_trigger_change(binding_str)
        on_mode_change(mode_str)
        on_model_change(model_name)
        on_language_change(lang_or_none)
        on_clean_fillers_change(bool)
        on_clean_llm_change(bool)
        on_sounds_change(bool)
        on_autostart_change(bool)
        on_show_overlay_change(bool)
        on_backend_change(backend_str)
        on_groq_api_key_change(key_str)
        on_cloud_stt_model_change(model_str)
        on_cloud_llm_model_change(model_str)
        ollama_available() -> bool
        groq_check(cfg) -> tuple[bool, str]  # flowlocal.cloud.check
        capture_next(callback) -> None  # TriggerManager.capture_next
        cancel_capture() -> None
    """
    import tkinter as tk
    from tkinter import ttk

    global _current_window

    if _current_window is not None and _current_window.winfo_exists():
        _current_window.deiconify()
        _current_window.lift()
        _current_window.focus_force()
        return

    win = tk.Toplevel(root)
    _current_window = win
    win.title("FlowLocal Settings")
    win.resizable(False, False)

    def _on_close():
        global _current_window
        cancel_capture = deps.get("cancel_capture")
        if cancel_capture:
            try:
                cancel_capture()
            except Exception:
                pass
        _current_window = None
        win.destroy()

    win.protocol("WM_DELETE_WINDOW", _on_close)

    refresh_devices = deps.get("refresh_devices")
    if refresh_devices:
        try:
            refresh_devices()
        except Exception:
            pass

    frame = ttk.Frame(win, padding=12)
    frame.grid(row=0, column=0, sticky="nsew")

    row = 0

    # --- Microphone ---------------------------------------------------
    ttk.Label(frame, text="Microphone:").grid(row=row, column=0, sticky="w", pady=4)
    list_devices = deps.get("list_devices")
    devices = list_devices() if list_devices else []
    mic_values = ["System default"] + [name for _idx, name in devices]
    mic_index_by_name = {name: idx for idx, name in devices}

    mic_var = tk.StringVar()
    current_name = "System default"
    if cfg.mic_device_name:
        for _idx, name in devices:
            if name == cfg.mic_device_name:
                current_name = name
                break
    if current_name == "System default" and cfg.mic_device is not None:
        for idx, name in devices:
            if idx == cfg.mic_device:
                current_name = name
                break
    mic_var.set(current_name)

    mic_row_frame = ttk.Frame(frame)
    mic_row_frame.grid(row=row, column=1, sticky="ew", pady=4)
    mic_combo = ttk.Combobox(
        mic_row_frame, textvariable=mic_var, values=mic_values, state="readonly", width=28
    )
    mic_combo.pack(side="left", fill="x", expand=True)

    def _on_refresh_devices() -> None:
        nonlocal devices, mic_values, mic_index_by_name
        refresh_devices = deps.get("refresh_devices")
        if refresh_devices:
            try:
                refresh_devices()
            except Exception:
                pass
        list_devices = deps.get("list_devices")
        devices = list_devices() if list_devices else []
        mic_values = ["System default"] + [name for _idx, name in devices]
        mic_index_by_name = {name: idx for idx, name in devices}
        mic_combo.config(values=mic_values)
        if mic_var.get() not in mic_values:
            mic_var.set("System default")

    refresh_btn = ttk.Button(mic_row_frame, text="↻", width=3, command=_on_refresh_devices)
    refresh_btn.pack(side="left", padx=(4, 0))
    row += 1

    # --- Trigger --------------------------------------------------------
    ttk.Label(frame, text="Trigger:").grid(row=row, column=0, sticky="w", pady=4)
    trigger_var = tk.StringVar(value=cfg.trigger)
    trigger_label = ttk.Label(frame, textvariable=trigger_var, width=20, relief="sunken")
    trigger_label.grid(row=row, column=1, sticky="w", pady=4)
    row += 1

    def _on_captured(binding: str) -> None:
        def _apply():
            trigger_var.set(binding)
            rebind_btn.config(text="Rebind…", state="normal")
            on_trigger_change = deps.get("on_trigger_change")
            if on_trigger_change:
                on_trigger_change(binding)

        win.after(0, _apply)

    def _start_capture() -> None:
        capture_next = deps.get("capture_next")
        if not capture_next:
            return
        rebind_btn.config(text="Press a key or mouse side button…", state="disabled")
        capture_next(_on_captured)

    rebind_btn = ttk.Button(frame, text="Rebind…", command=_start_capture)
    rebind_btn.grid(row=row, column=1, sticky="w", pady=(0, 8))
    row += 1

    # --- Mode -------------------------------------------------------------
    ttk.Label(frame, text="Mode:").grid(row=row, column=0, sticky="w", pady=4)
    mode_var = tk.StringVar(value=cfg.mode)
    mode_frame = ttk.Frame(frame)
    mode_frame.grid(row=row, column=1, sticky="w", pady=4)
    ttk.Radiobutton(mode_frame, text="Hold", variable=mode_var, value="hold").pack(
        side="left"
    )
    ttk.Radiobutton(mode_frame, text="Toggle", variable=mode_var, value="toggle").pack(
        side="left"
    )
    row += 1

    # --- Model --------------------------------------------------------
    ttk.Label(frame, text="Model:").grid(row=row, column=0, sticky="w", pady=4)
    model_var = tk.StringVar(value=_model_value_to_label(cfg.model))
    model_combo = ttk.Combobox(
        frame,
        textvariable=model_var,
        values=[lbl for _v, lbl in _MODEL_PRESETS],
        state="readonly",
        width=32,
    )
    model_combo.grid(row=row, column=1, sticky="ew", pady=4)
    row += 1

    # --- Language -----------------------------------------------------
    ttk.Label(frame, text="Language:").grid(row=row, column=0, sticky="w", pady=4)
    language_var = tk.StringVar(value=_language_code_to_label(cfg.language))
    language_combo = ttk.Combobox(
        frame,
        textvariable=language_var,
        values=[lbl for _code, lbl in _LANGUAGES],
        state="readonly",
        width=32,
    )
    language_combo.grid(row=row, column=1, sticky="ew", pady=4)
    row += 1

    # --- Processing (backend) --------------------------------------------
    ttk.Separator(frame, orient="horizontal").grid(
        row=row, column=0, columnspan=2, sticky="ew", pady=(8, 8)
    )
    row += 1

    ttk.Label(frame, text="Processing:").grid(row=row, column=0, sticky="w", pady=4)
    backend_var = tk.StringVar(value=_backend_value_to_label(cfg.backend))
    backend_combo = ttk.Combobox(
        frame,
        textvariable=backend_var,
        values=[lbl for _v, lbl in _BACKEND_PRESETS],
        state="readonly",
        width=32,
    )
    backend_combo.grid(row=row, column=1, sticky="ew", pady=4)
    row += 1

    ttk.Label(frame, text="Groq API key:").grid(row=row, column=0, sticky="w", pady=4)
    key_row_frame = ttk.Frame(frame)
    key_row_frame.grid(row=row, column=1, sticky="ew", pady=4)
    api_key_var = tk.StringVar(value=cfg.groq_api_key)
    api_key_entry = ttk.Entry(key_row_frame, textvariable=api_key_var, show="•", width=24)
    api_key_entry.pack(side="left", fill="x", expand=True)

    show_key_var = tk.BooleanVar(value=False)

    def _on_toggle_show_key() -> None:
        api_key_entry.config(show="" if show_key_var.get() else "•")

    ttk.Checkbutton(
        key_row_frame, text="Show", variable=show_key_var, command=_on_toggle_show_key
    ).pack(side="left", padx=(4, 0))
    row += 1

    ttk.Label(
        frame,
        text="Free API key: console.groq.com/keys",
        font=("TkDefaultFont", 8),
        foreground="#666666",
    ).grid(row=row, column=1, sticky="w", pady=(0, 4))
    row += 1

    key_missing_var = tk.StringVar(value="")
    key_missing_label = ttk.Label(frame, textvariable=key_missing_var, foreground="#b02020")
    key_missing_label.grid(row=row, column=1, sticky="w", pady=(0, 4))
    row += 1

    def _refresh_key_warning() -> None:
        backend_value = _backend_label_to_value(backend_var.get())
        if backend_value == "cloud" and not api_key_var.get().strip():
            key_missing_var.set("Cloud mode needs a Groq API key")
        else:
            key_missing_var.set("")

    backend_combo.bind("<<ComboboxSelected>>", lambda _e: _refresh_key_warning())
    api_key_entry.bind("<KeyRelease>", lambda _e: _refresh_key_warning())
    _refresh_key_warning()

    test_status_var = tk.StringVar(value="")
    test_row_frame = ttk.Frame(frame)
    test_row_frame.grid(row=row, column=1, sticky="w", pady=(0, 4))

    def _on_test_connection() -> None:
        groq_check = deps.get("groq_check")
        if not groq_check:
            return

        class _ProbeConfig:
            pass

        probe_cfg = _ProbeConfig()
        probe_cfg.groq_api_key = api_key_var.get()

        test_status_var.set("Testing…")

        def _run():
            try:
                ok, msg = groq_check(probe_cfg)
            except Exception as exc:
                ok, msg = False, str(exc)

            def _apply():
                test_status_var.set(("Connected" if ok else msg))

            try:
                win.after(0, _apply)
            except Exception:
                pass

        import threading as _threading

        _threading.Thread(target=_run, daemon=True).start()

    ttk.Button(test_row_frame, text="Test connection", command=_on_test_connection).pack(
        side="left"
    )
    ttk.Label(test_row_frame, textvariable=test_status_var).pack(side="left", padx=(6, 0))
    row += 1

    # --- Checkboxes -----------------------------------------------------
    fillers_var = tk.BooleanVar(value=cfg.clean_fillers)
    ttk.Checkbutton(frame, text="Remove filler words", variable=fillers_var).grid(
        row=row, column=0, columnspan=2, sticky="w", pady=2
    )
    row += 1

    ollama_available = deps.get("ollama_available")
    ollama_status = "detected" if (ollama_available and ollama_available()) else "not found"
    llm_var = tk.BooleanVar(value=cfg.clean_llm)
    ttk.Checkbutton(
        frame,
        text=f"LLM cleanup (Ollama: {ollama_status})",
        variable=llm_var,
    ).grid(row=row, column=0, columnspan=2, sticky="w", pady=2)
    row += 1

    sounds_var = tk.BooleanVar(value=cfg.sounds)
    ttk.Checkbutton(frame, text="Sounds", variable=sounds_var).grid(
        row=row, column=0, columnspan=2, sticky="w", pady=2
    )
    row += 1

    autostart_var = tk.BooleanVar(value=cfg.autostart)
    ttk.Checkbutton(frame, text="Start with Windows", variable=autostart_var).grid(
        row=row, column=0, columnspan=2, sticky="w", pady=2
    )
    row += 1

    show_overlay_var = tk.BooleanVar(value=cfg.show_overlay)
    ttk.Checkbutton(
        frame, text="Floating status circle + text popup", variable=show_overlay_var
    ).grid(row=row, column=0, columnspan=2, sticky="w", pady=2)
    row += 1

    # --- Save/Cancel ------------------------------------------------------
    button_frame = ttk.Frame(frame)
    button_frame.grid(row=row, column=0, columnspan=2, sticky="e", pady=(12, 0))

    def _on_save() -> None:
        mic_name_selected = mic_var.get()
        is_default = mic_name_selected == "System default"
        mic_index = mic_index_by_name.get(mic_name_selected) if not is_default else None
        mic_name = None if is_default else mic_name_selected
        if mic_index != cfg.mic_device or mic_name != cfg.mic_device_name:
            cfg.mic_device = mic_index
            cfg.mic_device_name = mic_name
            cb = deps.get("on_mic_change")
            if cb:
                cb(mic_index, mic_name)

        new_trigger = trigger_var.get()
        if new_trigger != cfg.trigger:
            cfg.trigger = new_trigger
            cb = deps.get("on_trigger_change")
            if cb:
                cb(new_trigger)

        new_mode = mode_var.get()
        if new_mode != cfg.mode:
            cfg.mode = new_mode
            cb = deps.get("on_mode_change")
            if cb:
                cb(new_mode)

        new_model = _model_label_to_value(model_var.get())
        if new_model and new_model != cfg.model:
            cfg.model = new_model
            cb = deps.get("on_model_change")
            if cb:
                cb(new_model)

        new_language = _language_label_to_code(language_var.get())
        if new_language != cfg.language:
            cfg.language = new_language
            cb = deps.get("on_language_change")
            if cb:
                cb(new_language)

        new_fillers = fillers_var.get()
        if new_fillers != cfg.clean_fillers:
            cfg.clean_fillers = new_fillers
            cb = deps.get("on_clean_fillers_change")
            if cb:
                cb(new_fillers)

        new_llm = llm_var.get()
        if new_llm != cfg.clean_llm:
            cfg.clean_llm = new_llm
            cb = deps.get("on_clean_llm_change")
            if cb:
                cb(new_llm)

        new_sounds = sounds_var.get()
        if new_sounds != cfg.sounds:
            cfg.sounds = new_sounds
            cb = deps.get("on_sounds_change")
            if cb:
                cb(new_sounds)

        new_autostart = autostart_var.get()
        if new_autostart != cfg.autostart:
            cfg.autostart = new_autostart
            cb = deps.get("on_autostart_change")
            if cb:
                cb(new_autostart)

        new_show_overlay = show_overlay_var.get()
        if new_show_overlay != cfg.show_overlay:
            cfg.show_overlay = new_show_overlay
            cb = deps.get("on_show_overlay_change")
            if cb:
                cb(new_show_overlay)

        new_api_key = api_key_var.get()
        if new_api_key != cfg.groq_api_key:
            cfg.groq_api_key = new_api_key
            cb = deps.get("on_groq_api_key_change")
            if cb:
                cb(new_api_key)

        new_backend = _backend_label_to_value(backend_var.get())
        if new_backend != cfg.backend:
            cfg.backend = new_backend
            cb = deps.get("on_backend_change")
            if cb:
                cb(new_backend)

        cfg.save()
        _on_close()

    ttk.Button(button_frame, text="Cancel", command=_on_close).pack(side="right", padx=(6, 0))
    ttk.Button(button_frame, text="Save", command=_on_save).pack(side="right")

    win.deiconify()
    win.lift()
    win.focus_force()
