"""Persistent floating status circle + on-demand text popup (tkinter),
bottom-center of the screen. Replaces the old always-on toast.

THREADING MODEL (must match flowlocal/app.py and flowlocal/settings_ui.py):
tkinter's Tcl interpreter is not thread-safe and must only be touched on
the process's MAIN thread. All widget creation/mutation in this module
happens on the main thread only. Since the dictation pipeline and trigger
callbacks run on background threads, this module exposes threadsafe
enqueue functions (`set_state_threadsafe`, `notify_result_threadsafe`,
`set_enabled_threadsafe`) that merely push onto a module-level
`queue.Queue` — safe to call from any thread. A periodic
`tk_root.after(...)` poller, started once via `start_poller(tk_root)` from
the main thread, drains that queue and applies the changes to the widget.
"""
from __future__ import annotations

import logging
import queue
from typing import Optional

logger = logging.getLogger(__name__)

_TRUNCATE_AT = 400
_POLL_INTERVAL_MS = 200

STATE_IDLE = "idle"
STATE_RECORDING = "recording"
STATE_TRANSCRIBING = "transcribing"

_MAGIC_TRANSPARENT_COLOR = "#010203"

_IDLE_COLOR = "#555555"
_RECORDING_COLOR = "#e0483e"
_TRANSCRIBING_COLOR = "#e0a53e"
_SUCCESS_COLOR = "#3ea05a"

_IDLE_RADIUS = 5
_RECORDING_RADII = (7, 9)
_TRANSCRIBING_RADII = (7, 9)
_SUCCESS_RADIUS = 8

_PULSE_INTERVAL_MS = 500
_SUCCESS_FLASH_MS = 600
_AUTO_EXPAND_MS = 5000
_LEAVE_GRACE_MS = 400
_COPIED_COLLAPSE_MS = 1000

_CIRCLE_SIZE = 36  # widget canvas size (square)
_BOTTOM_MARGIN = 60  # y = screen_h - _BOTTOM_MARGIN

_pending: "queue.Queue" = queue.Queue()

# Module-level singleton widget state (main thread only).
_widget = None  # type: Optional["_StatusCircle"]


class _Event:
    """Simple tagged event placed on the threadsafe queue."""

    __slots__ = ("kind", "payload")

    def __init__(self, kind: str, payload=None):
        self.kind = kind
        self.payload = payload


def _truncate(text: str) -> str:
    if len(text) <= _TRUNCATE_AT:
        return text
    return text[:_TRUNCATE_AT] + "…"


class _StatusCircle:
    """The persistent floating circle + on-demand popup panel."""

    def __init__(self, tk_root, enabled: bool = True) -> None:
        import tkinter as tk

        self._tk_root = tk_root
        self._enabled = enabled
        self._state = STATE_IDLE
        self._last_text: Optional[str] = None

        self._pulse_job = None
        self._pulse_phase = False
        self._success_job = None
        self._expand_job = None
        self._leave_job = None
        self._copied_job = None

        self._panel_visible = False
        self._pointer_over_circle = False
        self._pointer_over_panel = False

        screen_w = tk_root.winfo_screenwidth()
        screen_h = tk_root.winfo_screenheight()

        self._win = tk.Toplevel(tk_root)
        win = self._win
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        try:
            win.attributes("-toolwindow", True)
        except Exception:
            pass  # not all platforms support this attribute
        try:
            win.attributes("-transparentcolor", _MAGIC_TRANSPARENT_COLOR)
        except Exception as exc:
            logger.debug("transparentcolor unavailable: %s", exc)

        win.configure(bg=_MAGIC_TRANSPARENT_COLOR)

        size = _CIRCLE_SIZE
        x = (screen_w - size) // 2
        y = screen_h - _BOTTOM_MARGIN
        win.geometry(f"{size}x{size}+{x}+{y}")

        self._canvas = tk.Canvas(
            win,
            width=size,
            height=size,
            bg=_MAGIC_TRANSPARENT_COLOR,
            highlightthickness=0,
            bd=0,
        )
        self._canvas.pack(fill="both", expand=True)
        self._center = size // 2
        self._dot = self._canvas.create_oval(0, 0, 0, 0, fill=_IDLE_COLOR, outline="")

        self._panel: Optional["tk.Toplevel"] = None
        self._panel_label = None
        self._panel_hint_var = None

        self._canvas.bind("<Enter>", self._on_circle_enter)
        self._canvas.bind("<Leave>", self._on_circle_leave)

        self._redraw()
        self._apply_visibility()

    # --- visibility -----------------------------------------------------

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self._apply_visibility()

    def _apply_visibility(self) -> None:
        if self._enabled:
            try:
                self._win.deiconify()
            except Exception:
                pass
        else:
            self._collapse_panel(immediate=True)
            try:
                self._win.withdraw()
            except Exception:
                pass

    # --- state / drawing --------------------------------------------------

    def set_state(self, state: str) -> None:
        if state not in (STATE_IDLE, STATE_RECORDING, STATE_TRANSCRIBING):
            return
        self._state = state
        self._cancel_pulse()
        self._pulse_phase = False
        if state in (STATE_RECORDING, STATE_TRANSCRIBING):
            self._pulse()
        else:
            self._redraw()

    def _cancel_pulse(self) -> None:
        if self._pulse_job is not None:
            try:
                self._win.after_cancel(self._pulse_job)
            except Exception:
                pass
            self._pulse_job = None

    def _pulse(self) -> None:
        self._pulse_phase = not self._pulse_phase
        self._redraw()
        self._pulse_job = self._win.after(_PULSE_INTERVAL_MS, self._pulse)

    def _redraw(self) -> None:
        if self._success_job is not None:
            # A success flash is in progress; let it own the drawing until
            # it finishes.
            return

        if self._state == STATE_RECORDING:
            radius = _RECORDING_RADII[1] if self._pulse_phase else _RECORDING_RADII[0]
            color = _RECORDING_COLOR
        elif self._state == STATE_TRANSCRIBING:
            radius = (
                _TRANSCRIBING_RADII[1] if self._pulse_phase else _TRANSCRIBING_RADII[0]
            )
            color = _TRANSCRIBING_COLOR
        else:
            radius = _IDLE_RADIUS
            color = _IDLE_COLOR

        self._draw_dot(radius, color)

    def _draw_dot(self, radius: int, color: str) -> None:
        c = self._center
        try:
            self._canvas.coords(self._dot, c - radius, c - radius, c + radius, c + radius)
            self._canvas.itemconfig(self._dot, fill=color)
        except Exception:
            pass

    def flash_success(self) -> None:
        self._cancel_pulse()
        if self._success_job is not None:
            try:
                self._win.after_cancel(self._success_job)
            except Exception:
                pass
        self._draw_dot(_SUCCESS_RADIUS, _SUCCESS_COLOR)

        def _end_flash():
            self._success_job = None
            self._redraw()
            if self._state in (STATE_RECORDING, STATE_TRANSCRIBING):
                self._pulse()

        self._success_job = self._win.after(_SUCCESS_FLASH_MS, _end_flash)

    # --- result handling --------------------------------------------------

    def notify_result(self, text: str, landed_in_textbox: bool) -> None:
        self._last_text = text
        if landed_in_textbox:
            self.flash_success()
        else:
            self._expand_panel()
            self._schedule_auto_collapse(_AUTO_EXPAND_MS)

    # --- panel show/hide --------------------------------------------------

    def _on_circle_enter(self, _event=None) -> None:
        self._pointer_over_circle = True
        self._cancel_leave_grace()
        if self._last_text:
            self._expand_panel()

    def _on_circle_leave(self, _event=None) -> None:
        self._pointer_over_circle = False
        self._maybe_schedule_collapse()

    def _on_panel_enter(self, _event=None) -> None:
        self._pointer_over_panel = True
        self._cancel_leave_grace()

    def _on_panel_leave(self, _event=None) -> None:
        self._pointer_over_panel = False
        self._maybe_schedule_collapse()

    def _maybe_schedule_collapse(self) -> None:
        if not self._pointer_over_circle and not self._pointer_over_panel:
            self._cancel_leave_grace()
            self._leave_job = self._win.after(_LEAVE_GRACE_MS, self._collapse_panel)

    def _cancel_leave_grace(self) -> None:
        if self._leave_job is not None:
            try:
                self._win.after_cancel(self._leave_job)
            except Exception:
                pass
            self._leave_job = None

    def _cancel_auto_collapse(self) -> None:
        if self._expand_job is not None:
            try:
                self._win.after_cancel(self._expand_job)
            except Exception:
                pass
            self._expand_job = None

    def _schedule_auto_collapse(self, delay_ms: int) -> None:
        self._cancel_auto_collapse()
        self._expand_job = self._win.after(delay_ms, self._collapse_panel)

    def _expand_panel(self) -> None:
        import tkinter as tk

        if not self._enabled or not self._last_text:
            return

        self._cancel_auto_collapse()

        if self._panel is not None:
            self._update_panel_text()
            self._panel_visible = True
            return

        panel = tk.Toplevel(self._win)
        self._panel = panel
        panel.overrideredirect(True)
        panel.attributes("-topmost", True)
        try:
            panel.attributes("-toolwindow", True)
        except Exception:
            pass

        outer = tk.Frame(panel, bg="#1e1e1e", padx=16, pady=10)
        outer.pack(fill="both", expand=True)

        display_text = _truncate(self._last_text)
        label = tk.Label(
            outer,
            text=display_text,
            bg="#1e1e1e",
            fg="#eeeeee",
            font=("Segoe UI", 10),
            wraplength=600,
            justify="left",
            anchor="w",
        )
        label.pack(fill="x")
        self._panel_label = label

        hint_var = tk.StringVar(value="click to copy")
        self._panel_hint_var = hint_var
        hint_label = tk.Label(
            outer,
            textvariable=hint_var,
            bg="#1e1e1e",
            fg="#888888",
            font=("Segoe UI", 8),
            anchor="w",
        )
        hint_label.pack(fill="x", pady=(4, 0))

        for widget in (panel, outer, label, hint_label):
            widget.bind("<Enter>", self._on_panel_enter)
            widget.bind("<Leave>", self._on_panel_leave)
            widget.bind("<Button-1>", self._on_panel_click)

        panel.update_idletasks()
        panel_width = panel.winfo_reqwidth()
        panel_height = panel.winfo_reqheight()
        win_x = self._win.winfo_x()
        win_y = self._win.winfo_y()
        screen_width = self._win.winfo_screenwidth()
        x = (win_x + self._center) - panel_width // 2
        x = max(0, min(x, screen_width - panel_width))
        y = win_y - panel_height - 10
        panel.geometry(f"{panel_width}x{panel_height}+{x}+{y}")

        panel.lift()
        # Deliberately no focus_force()/focus_set()/grab_set(): must not
        # steal keyboard focus from whatever app the user is typing in.

        self._panel_visible = True

    def _update_panel_text(self) -> None:
        if self._panel_label is None or not self._last_text:
            return
        try:
            self._panel_label.config(text=_truncate(self._last_text))
            if self._panel_hint_var is not None:
                self._panel_hint_var.set("click to copy")
        except Exception:
            pass

    def _on_panel_click(self, _event=None) -> None:
        if not self._last_text:
            return
        try:
            self._tk_root.clipboard_clear()
            self._tk_root.clipboard_append(self._last_text)
            self._tk_root.update_idletasks()
        except Exception as exc:
            logger.debug("Clipboard copy failed: %s", exc)
            return

        self._cancel_auto_collapse()
        if self._panel_hint_var is not None:
            self._panel_hint_var.set("copied ✓")
        if self._copied_job is not None:
            try:
                self._win.after_cancel(self._copied_job)
            except Exception:
                pass
        self._copied_job = self._win.after(_COPIED_COLLAPSE_MS, self._collapse_panel)

    def _collapse_panel(self) -> None:
        self._cancel_leave_grace()
        self._cancel_auto_collapse()
        if self._copied_job is not None:
            try:
                self._win.after_cancel(self._copied_job)
            except Exception:
                pass
            self._copied_job = None

        self._panel_visible = False
        if self._panel is not None:
            try:
                self._panel.destroy()
            except Exception:
                pass
            self._panel = None
            self._panel_label = None
            self._panel_hint_var = None

    def destroy(self) -> None:
        self._cancel_pulse()
        self._collapse_panel()
        try:
            self._win.destroy()
        except Exception:
            pass


def set_state_threadsafe(state: str) -> None:
    """Enqueue a state change ('idle' | 'recording' | 'transcribing') to be
    applied on the main thread. Safe to call from any thread.
    """
    _pending.put(_Event("state", state))


def notify_result_threadsafe(text: str, landed_in_textbox: bool) -> None:
    """Enqueue a dictation result to be applied on the main thread. Safe to
    call from any thread.
    """
    _pending.put(_Event("result", (text, landed_in_textbox)))


def set_enabled_threadsafe(enabled: bool) -> None:
    """Enqueue an enable/disable toggle for the overlay widget. Safe to
    call from any thread.
    """
    _pending.put(_Event("enabled", enabled))


def start_poller(tk_root, interval_ms: int = _POLL_INTERVAL_MS, enabled: bool = True) -> None:
    """Create the persistent floating widget and start a periodic
    main-thread poller that drains the pending-event queue and applies
    changes to it. Call once from the main thread after the Tk root is
    created.
    """
    global _widget

    if _widget is None:
        try:
            _widget = _StatusCircle(tk_root, enabled=enabled)
        except Exception as exc:
            logger.error("Failed to create status overlay: %s", exc)
            _widget = None

    def _poll():
        try:
            while True:
                event = _pending.get_nowait()
                try:
                    _apply_event(event)
                except Exception as exc:
                    logger.error("Failed to apply overlay event: %s", exc)
        except queue.Empty:
            pass
        finally:
            tk_root.after(interval_ms, _poll)

    tk_root.after(interval_ms, _poll)


def _apply_event(event: "_Event") -> None:
    if _widget is None:
        return
    if event.kind == "state":
        _widget.set_state(event.payload)
    elif event.kind == "result":
        text, landed_in_textbox = event.payload
        _widget.notify_result(text, landed_in_textbox)
    elif event.kind == "enabled":
        _widget.set_enabled(bool(event.payload))
