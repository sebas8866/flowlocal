"""Settings page: every control from the old settings_ui.py, restyled into
cards, preserving the exact deps-dict callback contract from app.py's
`_open_settings` (same keys, same call semantics).

Apply semantics mirror the original settings_ui.py:
- Microphone, trigger, mode, model, language, cleanup toggles, sounds,
  autostart, floating pill, backend, Groq key, vocabulary text, max
  recording seconds: apply-on-save (batched into a single Save click),
  same as the original ttk form.
- Trigger rebind capture: applies immediately on capture (same as
  original — `_on_captured` calls `on_trigger_change` right away), Save
  merely no-ops for it since cfg.trigger already matches.
- Test connection: unchanged, threaded probe against the entry's current
  (possibly unsaved) key value.

New in this page (not in the old form): theme handled globally by the
sidebar toggle in window.py (not duplicated here), and "Save dictation
history" toggle wired to `on_save_history_change`.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, Dict, Optional

from flowlocal.ui import theme, widgets

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


def _label_for(pairs, value, default_index=0):
    for v, lbl in pairs:
        if v == value:
            return lbl
    return pairs[default_index][1]


def _value_for(pairs, label, default_index=0):
    for v, lbl in pairs:
        if lbl == label:
            return v
    return pairs[default_index][0]


class SettingsPage:
    def __init__(self, parent, cfg, deps: Dict[str, Callable], app_window) -> None:
        import customtkinter as ctk

        self.cfg = cfg
        self.deps = deps
        self.app_window = app_window

        self.frame = ctk.CTkScrollableFrame(parent, fg_color=theme.BG)
        self.frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self.frame, text="Settings", font=theme.font(20, "bold"), text_color=theme.TEXT
        ).grid(row=0, column=0, sticky="w", padx=theme.PAD_LG, pady=(theme.PAD_LG, theme.PAD_MD))

        row = 1
        row = self._build_input_card(row)
        row = self._build_processing_card(row)
        row = self._build_cleanup_card(row)
        row = self._build_general_card(row)
        row = self._build_save_row(row)

        self._refresh_key_warning()

    def grid(self, **kwargs):
        self.frame.grid(**kwargs)

    def tkraise(self):
        self.frame.tkraise()

    def on_show(self) -> None:
        refresh_devices = self.deps.get("refresh_devices")
        if refresh_devices:
            try:
                refresh_devices()
            except Exception:
                pass
        self._reload_devices()

    # --- Input (mic, trigger, mode) ---------------------------------------

    def _build_input_card(self, row: int) -> int:
        import customtkinter as ctk

        card, inner = widgets.make_card(self.frame)
        card.grid(row=row, column=0, sticky="ew", padx=theme.PAD_LG, pady=(0, theme.PAD_MD))
        widgets.card_header(inner, "Input").pack(anchor="w", pady=(0, theme.PAD_SM))

        # Microphone
        mic_row = ctk.CTkFrame(inner, fg_color="transparent")
        mic_row.pack(fill="x", pady=4)
        mic_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(mic_row, text="Microphone", font=theme.font(12), text_color=theme.TEXT, width=140, anchor="w").grid(row=0, column=0, sticky="w")

        list_devices = self.deps.get("list_devices")
        self._devices = list_devices() if list_devices else []
        mic_values = ["System default"] + [name for _idx, name in self._devices]
        self._mic_index_by_name = {name: idx for idx, name in self._devices}

        current_name = "System default"
        if self.cfg.mic_device_name:
            for _idx, name in self._devices:
                if name == self.cfg.mic_device_name:
                    current_name = name
                    break
        if current_name == "System default" and self.cfg.mic_device is not None:
            for idx, name in self._devices:
                if idx == self.cfg.mic_device:
                    current_name = name
                    break

        self._mic_var = ctk.StringVar(value=current_name)
        self._mic_menu = ctk.CTkOptionMenu(
            mic_row,
            variable=self._mic_var,
            values=mic_values,
            fg_color=theme.CARD_BG,
            button_color=theme.ACCENT,
            button_hover_color=theme.ACCENT_HOVER,
            text_color=theme.TEXT,
            dropdown_fg_color=theme.CARD_BG,
            dropdown_text_color=theme.TEXT,
        )
        self._mic_menu.grid(row=0, column=1, sticky="ew", padx=(theme.PAD_SM, theme.PAD_SM))

        widgets.icon_button(mic_row, "↻", command=self._on_refresh_devices, width=30).grid(row=0, column=2)

        # Trigger
        trigger_row = ctk.CTkFrame(inner, fg_color="transparent")
        trigger_row.pack(fill="x", pady=4)
        ctk.CTkLabel(trigger_row, text="Trigger", font=theme.font(12), text_color=theme.TEXT, width=140, anchor="w").pack(side="left")
        self._trigger_var = ctk.StringVar(value=self.cfg.trigger)
        ctk.CTkLabel(
            trigger_row, textvariable=self._trigger_var, font=theme.font(12), text_color=theme.TEXT_SECONDARY
        ).pack(side="left", padx=(0, theme.PAD_SM))
        self._rebind_btn = widgets.secondary_button(trigger_row, "Rebind…", command=self._start_capture)
        self._rebind_btn.pack(side="left")

        # Mode
        mode_row = ctk.CTkFrame(inner, fg_color="transparent")
        mode_row.pack(fill="x", pady=4)
        ctk.CTkLabel(mode_row, text="Mode", font=theme.font(12), text_color=theme.TEXT, width=140, anchor="w").pack(side="left")
        self._mode_var = ctk.StringVar(value=self.cfg.mode)
        ctk.CTkSegmentedButton(
            mode_row,
            variable=self._mode_var,
            values=["hold", "toggle"],
            fg_color=theme.SIDEBAR_BG,
            selected_color=theme.ACCENT,
            selected_hover_color=theme.ACCENT_HOVER,
            unselected_color=theme.SIDEBAR_BG,
            text_color=theme.TEXT,
        ).pack(side="left")

        return row + 1

    def _reload_devices(self) -> None:
        list_devices = self.deps.get("list_devices")
        self._devices = list_devices() if list_devices else []
        mic_values = ["System default"] + [name for _idx, name in self._devices]
        self._mic_index_by_name = {name: idx for idx, name in self._devices}
        self._mic_menu.configure(values=mic_values)
        if self._mic_var.get() not in mic_values:
            self._mic_var.set("System default")

    def _on_refresh_devices(self) -> None:
        refresh_devices = self.deps.get("refresh_devices")
        if refresh_devices:
            try:
                refresh_devices()
            except Exception:
                pass
        self._reload_devices()

    def _start_capture(self) -> None:
        capture_next = self.deps.get("capture_next")
        if not capture_next:
            return
        self._rebind_btn.configure(text="Press a key or mouse side button…", state="disabled")
        capture_next(self._on_captured)

    def _on_captured(self, binding: str) -> None:
        def _apply():
            self._trigger_var.set(binding)
            self._rebind_btn.configure(text="Rebind…", state="normal")
            cb = self.deps.get("on_trigger_change")
            if cb:
                cb(binding)

        try:
            self.frame.after(0, _apply)
        except Exception:
            pass

    # --- Processing (backend) ---------------------------------------------

    def _build_processing_card(self, row: int) -> int:
        import customtkinter as ctk

        card, inner = widgets.make_card(self.frame)
        card.grid(row=row, column=0, sticky="ew", padx=theme.PAD_LG, pady=(0, theme.PAD_MD))
        widgets.card_header(inner, "Processing").pack(anchor="w", pady=(0, theme.PAD_SM))

        backend_row = ctk.CTkFrame(inner, fg_color="transparent")
        backend_row.pack(fill="x", pady=4)
        backend_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(backend_row, text="Backend", font=theme.font(12), text_color=theme.TEXT, width=140, anchor="w").grid(row=0, column=0, sticky="w")

        self._backend_var = ctk.StringVar(value=_label_for(_BACKEND_PRESETS, self.cfg.backend))
        self._backend_menu = ctk.CTkOptionMenu(
            backend_row,
            variable=self._backend_var,
            values=[lbl for _v, lbl in _BACKEND_PRESETS],
            command=lambda _v: self._refresh_key_warning(),
            fg_color=theme.CARD_BG,
            button_color=theme.ACCENT,
            button_hover_color=theme.ACCENT_HOVER,
            text_color=theme.TEXT,
            dropdown_fg_color=theme.CARD_BG,
            dropdown_text_color=theme.TEXT,
        )
        self._backend_menu.grid(row=0, column=1, sticky="ew")

        # Groq key
        key_row = ctk.CTkFrame(inner, fg_color="transparent")
        key_row.pack(fill="x", pady=4)
        key_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(key_row, text="Groq API key", font=theme.font(12), text_color=theme.TEXT, width=140, anchor="w").grid(row=0, column=0, sticky="w")

        self._api_key_var = ctk.StringVar(value=self.cfg.groq_api_key)
        self._api_key_entry = ctk.CTkEntry(
            key_row,
            textvariable=self._api_key_var,
            show="•",
            corner_radius=theme.CORNER_RADIUS_SM,
            border_color=theme.CARD_BORDER,
            fg_color=theme.BG,
            text_color=theme.TEXT,
        )
        self._api_key_entry.grid(row=0, column=1, sticky="ew", padx=(0, theme.PAD_SM))
        self._api_key_entry.bind("<KeyRelease>", lambda _e: self._refresh_key_warning())

        self._show_key_var = ctk.BooleanVar(value=False)
        show_check = ctk.CTkCheckBox(
            key_row,
            text="Show",
            variable=self._show_key_var,
            command=self._on_toggle_show_key,
            font=theme.font(11),
            text_color=theme.TEXT_SECONDARY,
            fg_color=theme.ACCENT,
            hover_color=theme.ACCENT_HOVER,
        )
        show_check.grid(row=0, column=2)

        widgets.secondary_label(inner, "Free API key: console.groq.com/keys").pack(anchor="w", padx=(140 + theme.PAD_MD, 0))

        self._key_missing_label = ctk.CTkLabel(
            inner, text="", font=theme.font(11), text_color=theme.DANGER[0]
        )
        self._key_missing_label.pack(anchor="w", padx=(140 + theme.PAD_MD, 0))

        test_row = ctk.CTkFrame(inner, fg_color="transparent")
        test_row.pack(fill="x", pady=(4, 0), padx=(140 + theme.PAD_MD, 0))
        widgets.secondary_button(test_row, "Test connection", command=self._on_test_connection).pack(side="left")
        self._test_status_label = ctk.CTkLabel(
            test_row, text="", font=theme.font(11), text_color=theme.TEXT_SECONDARY
        )
        self._test_status_label.pack(side="left", padx=(theme.PAD_SM, 0))

        return row + 1

    def _on_toggle_show_key(self) -> None:
        self._api_key_entry.configure(show="" if self._show_key_var.get() else "•")

    def _refresh_key_warning(self) -> None:
        backend_value = _value_for(_BACKEND_PRESETS, self._backend_var.get())
        if backend_value == "cloud" and not self._api_key_var.get().strip():
            self._key_missing_label.configure(text="Cloud mode needs a Groq API key")
        else:
            self._key_missing_label.configure(text="")

    def _on_test_connection(self) -> None:
        groq_check = self.deps.get("groq_check")
        if not groq_check:
            return

        class _ProbeConfig:
            pass

        probe_cfg = _ProbeConfig()
        probe_cfg.groq_api_key = self._api_key_var.get()

        self._test_status_label.configure(text="Testing…")

        def _run():
            try:
                ok, msg = groq_check(probe_cfg)
            except Exception as exc:
                ok, msg = False, str(exc)

            def _apply():
                self._test_status_label.configure(text=("Connected" if ok else msg))

            try:
                self.frame.after(0, _apply)
            except Exception:
                pass

        threading.Thread(target=_run, daemon=True).start()

    # --- Model / language / cleanup toggles --------------------------------

    def _build_cleanup_card(self, row: int) -> int:
        import customtkinter as ctk

        card, inner = widgets.make_card(self.frame)
        card.grid(row=row, column=0, sticky="ew", padx=theme.PAD_LG, pady=(0, theme.PAD_MD))
        widgets.card_header(inner, "Transcription & cleanup").pack(anchor="w", pady=(0, theme.PAD_SM))

        model_row = ctk.CTkFrame(inner, fg_color="transparent")
        model_row.pack(fill="x", pady=4)
        model_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(model_row, text="Model", font=theme.font(12), text_color=theme.TEXT, width=140, anchor="w").grid(row=0, column=0, sticky="w")
        self._model_var = ctk.StringVar(value=_label_for(_MODEL_PRESETS, self.cfg.model))
        ctk.CTkOptionMenu(
            model_row,
            variable=self._model_var,
            values=[lbl for _v, lbl in _MODEL_PRESETS],
            fg_color=theme.CARD_BG,
            button_color=theme.ACCENT,
            button_hover_color=theme.ACCENT_HOVER,
            text_color=theme.TEXT,
            dropdown_fg_color=theme.CARD_BG,
            dropdown_text_color=theme.TEXT,
        ).grid(row=0, column=1, sticky="ew")

        lang_row = ctk.CTkFrame(inner, fg_color="transparent")
        lang_row.pack(fill="x", pady=4)
        lang_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(lang_row, text="Language", font=theme.font(12), text_color=theme.TEXT, width=140, anchor="w").grid(row=0, column=0, sticky="w")
        self._language_var = ctk.StringVar(value=_label_for(_LANGUAGES, self.cfg.language))
        ctk.CTkOptionMenu(
            lang_row,
            variable=self._language_var,
            values=[lbl for _c, lbl in _LANGUAGES],
            fg_color=theme.CARD_BG,
            button_color=theme.ACCENT,
            button_hover_color=theme.ACCENT_HOVER,
            text_color=theme.TEXT,
            dropdown_fg_color=theme.CARD_BG,
            dropdown_text_color=theme.TEXT,
        ).grid(row=0, column=1, sticky="ew")

        self._fillers_var = ctk.BooleanVar(value=self.cfg.clean_fillers)
        self._add_switch(inner, "Remove filler words", self._fillers_var)

        ollama_available = self.deps.get("ollama_available")
        ollama_status = "detected" if (ollama_available and ollama_available()) else "not found"
        self._llm_var = ctk.BooleanVar(value=self.cfg.clean_llm)
        self._add_switch(inner, f"LLM cleanup (Ollama: {ollama_status})", self._llm_var)

        self._smart_context_var = ctk.BooleanVar(value=self.cfg.smart_context)
        self._add_switch(inner, "App-aware tone matching", self._smart_context_var)

        self._voice_commands_var = ctk.BooleanVar(value=self.cfg.voice_commands)
        self._add_switch(inner, 'Voice commands ("new line", "scratch that", …)', self._voice_commands_var)

        return row + 1

    def _add_switch(self, parent, text: str, variable) -> None:
        import customtkinter as ctk

        ctk.CTkSwitch(
            parent,
            text=text,
            variable=variable,
            font=theme.font(12),
            text_color=theme.TEXT,
            progress_color=theme.ACCENT,
            button_color="#FFFFFF",
        ).pack(anchor="w", pady=3)

    # --- General (sounds, autostart, overlay, max seconds, history) --------

    def _build_general_card(self, row: int) -> int:
        import customtkinter as ctk

        card, inner = widgets.make_card(self.frame)
        card.grid(row=row, column=0, sticky="ew", padx=theme.PAD_LG, pady=(0, theme.PAD_MD))
        widgets.card_header(inner, "General").pack(anchor="w", pady=(0, theme.PAD_SM))

        self._sounds_var = ctk.BooleanVar(value=self.cfg.sounds)
        self._add_switch(inner, "Sounds", self._sounds_var)

        self._autostart_var = ctk.BooleanVar(value=self.cfg.autostart)
        self._add_switch(inner, "Start with Windows", self._autostart_var)

        self._show_overlay_var = ctk.BooleanVar(value=self.cfg.show_overlay)
        self._add_switch(inner, "Floating status circle + text popup", self._show_overlay_var)

        self._save_history_var = ctk.BooleanVar(value=self.cfg.save_history)
        self._add_switch(inner, "Save dictation history (stored locally only)", self._save_history_var)

        max_row = ctk.CTkFrame(inner, fg_color="transparent")
        max_row.pack(fill="x", pady=(8, 4))
        ctk.CTkLabel(max_row, text="Max recording seconds", font=theme.font(12), text_color=theme.TEXT, width=180, anchor="w").pack(side="left")
        self._max_seconds_var = ctk.StringVar(value=str(self.cfg.max_record_seconds))
        ctk.CTkEntry(
            max_row,
            textvariable=self._max_seconds_var,
            width=80,
            corner_radius=theme.CORNER_RADIUS_SM,
            border_color=theme.CARD_BORDER,
            fg_color=theme.BG,
            text_color=theme.TEXT,
        ).pack(side="left")

        return row + 1

    # --- Save ---------------------------------------------------------------

    def _build_save_row(self, row: int) -> int:
        import customtkinter as ctk

        btn_row = ctk.CTkFrame(self.frame, fg_color="transparent")
        btn_row.grid(row=row, column=0, sticky="e", padx=theme.PAD_LG, pady=(0, theme.PAD_LG))
        self._save_status_label = ctk.CTkLabel(
            btn_row, text="", font=theme.font(11), text_color=theme.SUCCESS[0]
        )
        self._save_status_label.pack(side="left", padx=(0, theme.PAD_SM))
        widgets.primary_button(btn_row, "Save", command=self._on_save).pack(side="right")
        return row + 1

    def _on_save(self) -> None:
        cfg = self.cfg
        deps = self.deps

        mic_name_selected = self._mic_var.get()
        is_default = mic_name_selected == "System default"
        mic_index = self._mic_index_by_name.get(mic_name_selected) if not is_default else None
        mic_name = None if is_default else mic_name_selected
        if mic_index != cfg.mic_device or mic_name != cfg.mic_device_name:
            cfg.mic_device = mic_index
            cfg.mic_device_name = mic_name
            cb = deps.get("on_mic_change")
            if cb:
                cb(mic_index, mic_name)

        new_trigger = self._trigger_var.get()
        if new_trigger != cfg.trigger:
            cfg.trigger = new_trigger
            cb = deps.get("on_trigger_change")
            if cb:
                cb(new_trigger)

        new_mode = self._mode_var.get()
        if new_mode != cfg.mode:
            cfg.mode = new_mode
            cb = deps.get("on_mode_change")
            if cb:
                cb(new_mode)

        new_model = _value_for(_MODEL_PRESETS, self._model_var.get())
        if new_model and new_model != cfg.model:
            cfg.model = new_model
            cb = deps.get("on_model_change")
            if cb:
                cb(new_model)

        new_language = _value_for(_LANGUAGES, self._language_var.get())
        if new_language != cfg.language:
            cfg.language = new_language
            cb = deps.get("on_language_change")
            if cb:
                cb(new_language)

        new_fillers = self._fillers_var.get()
        if new_fillers != cfg.clean_fillers:
            cfg.clean_fillers = new_fillers
            cb = deps.get("on_clean_fillers_change")
            if cb:
                cb(new_fillers)

        new_llm = self._llm_var.get()
        if new_llm != cfg.clean_llm:
            cfg.clean_llm = new_llm
            cb = deps.get("on_clean_llm_change")
            if cb:
                cb(new_llm)

        new_smart_context = self._smart_context_var.get()
        if new_smart_context != cfg.smart_context:
            cfg.smart_context = new_smart_context
            cb = deps.get("on_smart_context_change")
            if cb:
                cb(new_smart_context)

        new_voice_commands = self._voice_commands_var.get()
        if new_voice_commands != cfg.voice_commands:
            cfg.voice_commands = new_voice_commands
            cb = deps.get("on_voice_commands_change")
            if cb:
                cb(new_voice_commands)

        new_sounds = self._sounds_var.get()
        if new_sounds != cfg.sounds:
            cfg.sounds = new_sounds
            cb = deps.get("on_sounds_change")
            if cb:
                cb(new_sounds)

        new_autostart = self._autostart_var.get()
        if new_autostart != cfg.autostart:
            cfg.autostart = new_autostart
            cb = deps.get("on_autostart_change")
            if cb:
                cb(new_autostart)

        new_show_overlay = self._show_overlay_var.get()
        if new_show_overlay != cfg.show_overlay:
            cfg.show_overlay = new_show_overlay
            cb = deps.get("on_show_overlay_change")
            if cb:
                cb(new_show_overlay)

        new_save_history = self._save_history_var.get()
        if new_save_history != cfg.save_history:
            cfg.save_history = new_save_history
            cb = deps.get("on_save_history_change")
            if cb:
                cb(new_save_history)

        try:
            new_max_seconds = int(self._max_seconds_var.get())
            if new_max_seconds > 0 and new_max_seconds != cfg.max_record_seconds:
                cfg.max_record_seconds = new_max_seconds
        except ValueError:
            self._max_seconds_var.set(str(cfg.max_record_seconds))

        new_api_key = self._api_key_var.get()
        if new_api_key != cfg.groq_api_key:
            cfg.groq_api_key = new_api_key
            cb = deps.get("on_groq_api_key_change")
            if cb:
                cb(new_api_key)

        new_backend = _value_for(_BACKEND_PRESETS, self._backend_var.get())
        if new_backend != cfg.backend:
            cfg.backend = new_backend
            cb = deps.get("on_backend_change")
            if cb:
                cb(new_backend)

        cfg.save()

        self._save_status_label.configure(text="Saved")
        self.frame.after(1500, lambda: self._save_status_label.configure(text=""))
