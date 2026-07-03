"""Persistent floating status pill + on-demand text popup (tkinter),
bottom-center of the screen. Replaces the old always-on toast.

THREADING MODEL (must match flowlocal/app.py and flowlocal/ui/window.py):
tkinter's Tcl interpreter is not thread-safe and must only be touched on
the process's MAIN thread. All widget creation/mutation in this module
happens on the main thread only. Since the dictation pipeline and trigger
callbacks run on background threads, this module exposes threadsafe
enqueue functions (`set_state_threadsafe`, `notify_result_threadsafe`,
`set_enabled_threadsafe`) that merely push onto a module-level
`queue.Queue` — safe to call from any thread. A periodic
`tk_root.after(...)` poller, started once via `start_poller(tk_root)` from
the main thread, drains that queue and applies the changes to the widget.

Microphone loudness updates (`set_level_threadsafe`, called at audio-block
rate from the recorder's callback thread) are coalesced into a single
module-level slot rather than queued, since only the latest value ever
matters. They are consumed by the widget's own 50ms animation tick while
recording, not by the 200ms queue poller.
"""
from __future__ import annotations

import logging
import queue
from collections import deque
from typing import Optional

logger = logging.getLogger(__name__)

_TRUNCATE_AT = 400
_POLL_INTERVAL_MS = 200
_ANIM_INTERVAL_MS = 50

STATE_IDLE = "idle"
STATE_RECORDING = "recording"
STATE_TRANSCRIBING = "transcribing"

_MAGIC_TRANSPARENT_COLOR = "#010203"

_PILL_FILL = "#1c1c1c"
_PILL_BORDER = "#3a3a3a"
_SUCCESS_FILL = "#1c2e20"
_SUCCESS_BORDER = "#3ea05a"
_BAR_COLOR = "#e8e8e8"
_BAR_DIM_COLOR = "#4a4a4a"
_SHIMMER_COLOR = "#8a8a8a"

_SUCCESS_FLASH_MS = 500
_AUTO_EXPAND_MS = 5000
_LEAVE_GRACE_MS = 400
_COPIED_COLLAPSE_MS = 1000

# Popup panel ("Wispr-clean" white card) — matches flowlocal/ui/theme.py
# light tokens (PANEL_BG/CARD_BORDER/TEXT/TEXT_SECONDARY/ACCENT).
_PANEL_BG = "#FFFFFF"
_PANEL_BORDER = "#E8E4DC"
_PANEL_RADIUS = 12
_PANEL_TEXT_COLOR = "#1A1A1A"
_PANEL_HINT_COLOR = "#6B6660"
_PANEL_ACCENT = "#E8623D"
_PANEL_PAD_X = 16
_PANEL_PAD_Y = 14
_PANEL_MAX_WIDTH = 420
_PANEL_ACCENT_STRIP_W = 3
_PANEL_MAX_TEXT_LINES = 8

# Idle (thin line) pill geometry.
_IDLE_W = 64
_IDLE_H = 10
# Expanded (recording/transcribing) pill geometry.
_EXPANDED_W = 150
_EXPANDED_H = 30

_GROW_STEPS = 4
_GROW_STEP_MS = 150 // _GROW_STEPS

# Waveform bars.
_BAR_COUNT = 28
_BAR_WIDTH = 3
_BAR_GAP = 2
_BAR_MIN_H = 3
_BAR_MAX_EXTRA_H = 20
_LEVEL_DECAY = 0.8

_BOTTOM_MARGIN = 10  # y = screen_h - height - _BOTTOM_MARGIN
# Widget canvas is sized to the largest state so it never needs to be
# resized/repositioned mid-animation; we only redraw within it.
_CANVAS_W = _EXPANDED_W
_CANVAS_H = _EXPANDED_H

_pending: "queue.Queue" = queue.Queue()

# Coalesced latest microphone level (0..1), separate from `_pending` since
# only the most recent value ever matters — no point queuing a backlog of
# level updates from the audio callback thread.
_pending_level: Optional[float] = None

# Module-level singleton widget state (main thread only).
_widget = None  # type: Optional["_StatusPill"]


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


class _StatusPill:
    """The persistent floating bottom pill + on-demand popup panel."""

    def __init__(self, tk_root, enabled: bool = True) -> None:
        import tkinter as tk

        self._tk_root = tk_root
        self._enabled = enabled
        self._state = STATE_IDLE
        self._last_text: Optional[str] = None

        self._grow_job = None
        self._success_job = None
        self._expand_job = None
        self._leave_job = None
        self._copied_job = None
        self._anim_job = None

        self._panel_visible = False
        self._pointer_over_pill = False
        self._pointer_over_panel = False

        # Current animated pill size (grows/shrinks in steps).
        self._cur_w = float(_IDLE_W)
        self._cur_h = float(_IDLE_H)
        self._target_w = float(_IDLE_W)
        self._target_h = float(_IDLE_H)

        # Rolling ring buffer of recent loudness levels for the waveform.
        self._levels: "deque" = deque([0.0] * _BAR_COUNT, maxlen=_BAR_COUNT)
        self._shimmer_pos = 0.0

        screen_w = tk_root.winfo_screenwidth()
        screen_h = tk_root.winfo_screenheight()
        self._screen_w = screen_w
        self._screen_h = screen_h

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

        # The window itself is sized to the largest (expanded) footprint so
        # growing/shrinking never has to move or resize the Toplevel; we
        # just redraw the pill within it.
        self._win_w = _CANVAS_W
        self._win_h = _CANVAS_H
        win_x = (screen_w - self._win_w) // 2
        win_y = screen_h - self._win_h - _BOTTOM_MARGIN
        self._win_x = win_x
        self._win_y = win_y
        win.geometry(f"{self._win_w}x{self._win_h}+{win_x}+{win_y}")

        self._canvas = tk.Canvas(
            win,
            width=self._win_w,
            height=self._win_h,
            bg=_MAGIC_TRANSPARENT_COLOR,
            highlightthickness=0,
            bd=0,
        )
        self._canvas.pack(fill="both", expand=True)
        self._center_x = self._win_w // 2
        self._center_y = self._win_h // 2

        self._pill_items: list = []  # canvas item ids composing the pill shape
        self._bar_items: list = []  # canvas item ids for waveform bars

        self._panel: Optional["tk.Toplevel"] = None
        self._panel_canvas = None
        self._panel_label = None
        self._panel_hint_var = None
        self._panel_hint_label = None

        self._canvas.bind("<Enter>", self._on_pill_enter)
        self._canvas.bind("<Leave>", self._on_pill_leave)

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
            self._collapse_panel()
            try:
                self._win.withdraw()
            except Exception:
                pass

    # --- state / sizing -----------------------------------------------------

    def set_state(self, state: str) -> None:
        if state not in (STATE_IDLE, STATE_RECORDING, STATE_TRANSCRIBING):
            return
        prev_state = self._state
        self._state = state

        if state in (STATE_RECORDING, STATE_TRANSCRIBING):
            self._target_w = float(_EXPANDED_W)
            self._target_h = float(_EXPANDED_H)
        else:
            self._target_w = float(_IDLE_W)
            self._target_h = float(_IDLE_H)
            if prev_state != STATE_IDLE:
                self._levels = deque([0.0] * _BAR_COUNT, maxlen=_BAR_COUNT)

        self._cancel_grow()
        self._animate_grow()
        self._update_anim_tick()

    def _cancel_grow(self) -> None:
        if self._grow_job is not None:
            try:
                self._win.after_cancel(self._grow_job)
            except Exception:
                pass
            self._grow_job = None

    def _animate_grow(self) -> None:
        dw = self._target_w - self._cur_w
        dh = self._target_h - self._cur_h
        if abs(dw) < 0.5 and abs(dh) < 0.5:
            self._cur_w = self._target_w
            self._cur_h = self._target_h
            self._grow_job = None
            self._redraw()
            return

        self._cur_w += dw / _GROW_STEPS
        self._cur_h += dh / _GROW_STEPS
        self._redraw()
        self._grow_job = self._win.after(_GROW_STEP_MS, self._animate_grow)

    # --- animation tick (waveform flow / shimmer) --------------------------

    def _update_anim_tick(self) -> None:
        should_run = self._state in (STATE_RECORDING, STATE_TRANSCRIBING)
        if should_run and self._anim_job is None:
            self._anim_job = self._win.after(_ANIM_INTERVAL_MS, self._on_anim_tick)
        elif not should_run and self._anim_job is not None:
            try:
                self._win.after_cancel(self._anim_job)
            except Exception:
                pass
            self._anim_job = None

    def _on_anim_tick(self) -> None:
        global _pending_level

        self._anim_job = None
        if self._state == STATE_RECORDING:
            level = _pending_level
            if level is not None:
                _pending_level = None
                try:
                    level = max(0.0, min(1.0, float(level)))
                except (TypeError, ValueError):
                    level = None
            if level is not None:
                self._levels.append(level)
            else:
                # No fresh level arrived this tick: keep the wave flowing by
                # shifting in a decayed copy of the last value.
                last = self._levels[-1] if self._levels else 0.0
                self._levels.append(last * _LEVEL_DECAY)
        elif self._state == STATE_TRANSCRIBING:
            self._shimmer_pos = (self._shimmer_pos + 1) % (_BAR_COUNT + 6)

        self._redraw()

        if self._state in (STATE_RECORDING, STATE_TRANSCRIBING):
            self._anim_job = self._win.after(_ANIM_INTERVAL_MS, self._on_anim_tick)

    # --- drawing ------------------------------------------------------------

    def _redraw(self) -> None:
        if self._success_job is not None:
            # A success flash is in progress; let it own the drawing until
            # it finishes.
            return
        self._draw_pill()

    def _clear_pill(self) -> None:
        for item in self._pill_items:
            try:
                self._canvas.delete(item)
            except Exception:
                pass
        self._pill_items = []
        for item in self._bar_items:
            try:
                self._canvas.delete(item)
            except Exception:
                pass
        self._bar_items = []

    def _draw_pill(self, fill: str = _PILL_FILL, border: str = _PILL_BORDER) -> None:
        self._clear_pill()
        try:
            cx, cy = self._center_x, self._center_y
            w, h = self._cur_w, self._cur_h
            r = h / 2.0
            left = cx - w / 2.0
            right = cx + w / 2.0
            top = cy - r
            bottom = cy + r

            # Rounded pill: two end-caps (ovals) + a connecting rectangle.
            self._pill_items.append(
                self._canvas.create_oval(
                    left, top, left + h, bottom, fill=fill, outline=border, width=1
                )
            )
            self._pill_items.append(
                self._canvas.create_oval(
                    right - h, top, right, bottom, fill=fill, outline=border, width=1
                )
            )
            self._pill_items.append(
                self._canvas.create_rectangle(
                    left + r, top, right - r, bottom, fill=fill, outline=""
                )
            )
            # Top/bottom border lines across the straight middle section
            # (ovals already draw the border on the curved ends).
            self._pill_items.append(
                self._canvas.create_line(left + r, top, right - r, top, fill=border)
            )
            self._pill_items.append(
                self._canvas.create_line(left + r, bottom, right - r, bottom, fill=border)
            )

            expanded = self._cur_h > (_IDLE_H + 2)
            if expanded and self._state == STATE_RECORDING:
                self._draw_waveform(cx, cy, w, h)
            elif expanded and self._state == STATE_TRANSCRIBING:
                self._draw_shimmer(cx, cy, w, h)
        except Exception:
            pass

    def _draw_waveform(self, cx: float, cy: float, w: float, h: float) -> None:
        n = len(self._levels)
        if n == 0:
            return
        step = _BAR_WIDTH + _BAR_GAP
        total_w = n * step - _BAR_GAP
        start_x = cx - total_w / 2.0
        for i, level in enumerate(self._levels):
            bar_h = _BAR_MIN_H + level * _BAR_MAX_EXTRA_H
            bar_h = min(bar_h, h - 4)
            x0 = start_x + i * step
            x1 = x0 + _BAR_WIDTH
            y0 = cy - bar_h / 2.0
            y1 = cy + bar_h / 2.0
            self._bar_items.append(
                self._canvas.create_rectangle(
                    x0, y0, x1, y1, fill=_BAR_COLOR, outline="", width=0
                )
            )

    def _draw_shimmer(self, cx: float, cy: float, w: float, h: float) -> None:
        n = _BAR_COUNT
        step = _BAR_WIDTH + _BAR_GAP
        total_w = n * step - _BAR_GAP
        start_x = cx - total_w / 2.0
        base_h = _BAR_MIN_H + 3
        band_width = 5
        for i in range(n):
            # Distance from the moving shimmer band (wraps around).
            dist = min(
                abs(i - self._shimmer_pos),
                abs(i - self._shimmer_pos + n + 6),
                abs(i - self._shimmer_pos - n - 6),
            )
            if dist <= band_width:
                brightness = 1.0 - (dist / band_width)
                color = _SHIMMER_COLOR if brightness > 0.4 else _BAR_DIM_COLOR
                bar_h = base_h + brightness * 6
            else:
                color = _BAR_DIM_COLOR
                bar_h = base_h
            x0 = start_x + i * step
            x1 = x0 + _BAR_WIDTH
            y0 = cy - bar_h / 2.0
            y1 = cy + bar_h / 2.0
            self._bar_items.append(
                self._canvas.create_rectangle(
                    x0, y0, x1, y1, fill=color, outline="", width=0
                )
            )

    def flash_success(self) -> None:
        if self._success_job is not None:
            try:
                self._win.after_cancel(self._success_job)
            except Exception:
                pass
        self._draw_pill(fill=_SUCCESS_FILL, border=_SUCCESS_BORDER)

        def _end_flash():
            self._success_job = None
            # Shrink back to idle after the flash.
            self.set_state(STATE_IDLE)

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

    def _on_pill_enter(self, _event=None) -> None:
        self._pointer_over_pill = True
        self._cancel_leave_grace()
        if self._last_text:
            self._expand_panel()

    def _on_pill_leave(self, _event=None) -> None:
        self._pointer_over_pill = False
        self._maybe_schedule_collapse()

    def _on_panel_enter(self, _event=None) -> None:
        self._pointer_over_panel = True
        self._cancel_leave_grace()

    def _on_panel_leave(self, _event=None) -> None:
        self._pointer_over_panel = False
        self._maybe_schedule_collapse()

    def _maybe_schedule_collapse(self) -> None:
        if not self._pointer_over_pill and not self._pointer_over_panel:
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

    def _draw_rounded_rect(self, canvas, x0, y0, x1, y1, radius, **kwargs) -> int:
        """Draw a filled/outlined rounded rectangle on `canvas` via a smoothed
        polygon (the standard tkinter rounded-rect trick) and return its item
        id. Same genuinely-rounded-corners approach the pill uses, adapted
        from ovals+rect (pill, capsule-shaped) to a smoothed polygon (panel,
        a real rectangle with corner radius)."""
        points = [
            x0 + radius, y0,
            x1 - radius, y0,
            x1, y0,
            x1, y0 + radius,
            x1, y1 - radius,
            x1, y1,
            x1 - radius, y1,
            x0 + radius, y1,
            x0, y1,
            x0, y1 - radius,
            x0, y0 + radius,
            x0, y0,
        ]
        return canvas.create_polygon(points, smooth=True, **kwargs)

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
        try:
            panel.attributes("-transparentcolor", _MAGIC_TRANSPARENT_COLOR)
        except Exception as exc:
            logger.debug("transparentcolor unavailable for panel: %s", exc)
        panel.configure(bg=_MAGIC_TRANSPARENT_COLOR)

        # Measure the wrapped text first (off-screen label) so we can size
        # the card, then draw the rounded card on a canvas and lay the text
        # + hint on top of it via create_window — keeps the transparent-key
        # rounding technique consistent with the pill.
        display_text = _truncate(self._last_text)
        wrap_width = _PANEL_MAX_WIDTH - 2 * _PANEL_PAD_X - _PANEL_ACCENT_STRIP_W

        probe = tk.Label(
            panel,
            text=display_text,
            font=("Segoe UI", 11),
            wraplength=wrap_width,
            justify="left",
        )
        probe.update_idletasks()
        text_w = min(probe.winfo_reqwidth(), wrap_width)
        text_h = probe.winfo_reqheight()
        max_text_h = _PANEL_MAX_TEXT_LINES * 16
        if text_h > max_text_h:
            text_h = max_text_h
        probe.destroy()

        card_w = text_w + 2 * _PANEL_PAD_X + _PANEL_ACCENT_STRIP_W
        card_w = min(max(card_w, 160), _PANEL_MAX_WIDTH)
        hint_h = 16
        card_h = _PANEL_PAD_Y + text_h + 4 + hint_h + _PANEL_PAD_Y

        canvas = tk.Canvas(
            panel,
            width=card_w,
            height=card_h,
            bg=_MAGIC_TRANSPARENT_COLOR,
            highlightthickness=0,
            bd=0,
        )
        canvas.pack(fill="both", expand=True)
        self._panel_canvas = canvas

        self._draw_rounded_rect(
            canvas, 0, 0, card_w, card_h, _PANEL_RADIUS,
            fill=_PANEL_BG, outline=_PANEL_BORDER, width=1,
        )
        # Subtle coral accent strip along the card's left edge, inset
        # slightly so it stays inside the card's own rounded corners.
        strip_inset = _PANEL_RADIUS * 0.4
        self._draw_rounded_rect(
            canvas,
            2, strip_inset, 2 + _PANEL_ACCENT_STRIP_W, card_h - strip_inset,
            _PANEL_ACCENT_STRIP_W / 2.0,
            fill=_PANEL_ACCENT, outline="",
        )

        text_x = _PANEL_ACCENT_STRIP_W + _PANEL_PAD_X
        label = tk.Label(
            canvas,
            text=display_text,
            bg=_PANEL_BG,
            fg=_PANEL_TEXT_COLOR,
            font=("Segoe UI", 11),
            wraplength=wrap_width,
            justify="left",
            anchor="nw",
        )
        canvas.create_window(
            text_x, _PANEL_PAD_Y, width=text_w, height=text_h,
            window=label, anchor="nw",
        )
        self._panel_label = label

        hint_var = tk.StringVar(value="Click to copy")
        self._panel_hint_var = hint_var
        hint_label = tk.Label(
            canvas,
            textvariable=hint_var,
            bg=_PANEL_BG,
            fg=_PANEL_HINT_COLOR,
            font=("Segoe UI", 9),
            anchor="w",
        )
        canvas.create_window(
            text_x, _PANEL_PAD_Y + text_h + 4, width=text_w, height=hint_h,
            window=hint_label, anchor="nw",
        )
        self._panel_hint_label = hint_label

        for widget in (panel, canvas, label, hint_label):
            widget.bind("<Enter>", self._on_panel_enter)
            widget.bind("<Leave>", self._on_panel_leave)
            widget.bind("<Button-1>", self._on_panel_click)

        panel_width = card_w
        panel_height = card_h
        win_x = self._win.winfo_x()
        win_y = self._win.winfo_y()
        screen_width = self._win.winfo_screenwidth()
        # Anchor above the pill's horizontal center; the pill's canvas is
        # always full window width so the window x-center is the pill center.
        x = (win_x + self._center_x) - panel_width // 2
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
                self._panel_hint_var.set("Click to copy")
            if self._panel_hint_label is not None:
                self._panel_hint_label.config(fg=_PANEL_HINT_COLOR)
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
            self._panel_hint_var.set("Copied ✓")
        if self._panel_hint_label is not None:
            try:
                self._panel_hint_label.config(fg=_PANEL_ACCENT)
            except Exception:
                pass
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
            self._panel_canvas = None
            self._panel_label = None
            self._panel_hint_var = None
            self._panel_hint_label = None

    def destroy(self) -> None:
        self._cancel_grow()
        if self._anim_job is not None:
            try:
                self._win.after_cancel(self._anim_job)
            except Exception:
                pass
            self._anim_job = None
        if self._success_job is not None:
            try:
                self._win.after_cancel(self._success_job)
            except Exception:
                pass
            self._success_job = None
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


def set_level_threadsafe(level: float) -> None:
    """Report the current microphone loudness (0..1) for the live
    waveform. Safe to call from any thread (including the audio callback
    thread). Coalesced: only the latest level is kept if the queue hasn't
    been drained yet, so a burst of audio-callback calls can't build up a
    backlog.
    """
    global _pending_level
    _pending_level = float(level)


def start_poller(tk_root, interval_ms: int = _POLL_INTERVAL_MS, enabled: bool = True) -> None:
    """Create the persistent floating widget and start a periodic
    main-thread poller that drains the pending-event queue and applies
    changes to it. Call once from the main thread after the Tk root is
    created.
    """
    global _widget

    if _widget is None:
        try:
            _widget = _StatusPill(tk_root, enabled=enabled)
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
