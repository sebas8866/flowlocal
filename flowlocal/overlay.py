"""Post-dictation text overlay toast (tkinter), bottom-center of the screen.

THREADING MODEL (must match flowlocal/app.py and flowlocal/settings_ui.py):
tkinter's Tcl interpreter is not thread-safe and must only be touched on the
process's MAIN thread. `show_toast` itself must therefore only be called on
the main thread. Since the dictation pipeline runs on a background worker
thread, this module exposes `show_toast_threadsafe(text)` which merely
enqueues text onto a module-level `queue.Queue` — safe to call from any
thread. A periodic `tk_root.after(...)` poller, started once via
`start_poller(tk_root)` from the main thread, drains that queue and calls
`show_toast` for each pending item.
"""
from __future__ import annotations

import logging
import queue
from typing import Optional

logger = logging.getLogger(__name__)

_TRUNCATE_AT = 400
_POLL_INTERVAL_MS = 200

_pending: "queue.Queue" = queue.Queue()
_current_toast = None  # module-level singleton guard


def _truncate(text: str) -> str:
    if len(text) <= _TRUNCATE_AT:
        return text
    return text[:_TRUNCATE_AT] + "…"


def show_toast(tk_root, text: str, duration_ms: int = 5000) -> None:
    """Show a borderless, always-on-top, non-focus-stealing toast at the
    bottom-center of the primary screen. Must be called on the main thread.

    Clicking the toast copies the full `text` to the clipboard. Hovering
    pauses the auto-close countdown; leaving restarts it. Showing a new
    toast destroys any previous one.
    """
    import tkinter as tk

    global _current_toast

    if _current_toast is not None:
        try:
            _current_toast.destroy()
        except Exception:
            pass
        _current_toast = None

    win = tk.Toplevel(tk_root)
    _current_toast = win
    win.overrideredirect(True)
    win.attributes("-topmost", True)
    try:
        win.attributes("-toolwindow", True)
    except Exception:
        pass  # not all platforms support this attribute

    outer = tk.Frame(win, bg="#1e1e1e", padx=16, pady=10)
    outer.pack(fill="both", expand=True)

    display_text = _truncate(text)
    label = tk.Label(
        outer,
        text=display_text,
        bg="#1e1e1e",
        fg="#eeeeee",
        font=("Segoe UI", 10),
        wraplength=568,
        justify="left",
        anchor="w",
    )
    label.pack(fill="x")

    hint_var = tk.StringVar(value="click to copy")
    hint_label = tk.Label(
        outer,
        textvariable=hint_var,
        bg="#1e1e1e",
        fg="#888888",
        font=("Segoe UI", 8),
        anchor="w",
    )
    hint_label.pack(fill="x", pady=(4, 0))

    widgets = [win, outer, label, hint_label]

    close_job: Optional[str] = None
    copy_close_job: Optional[str] = None

    def _destroy() -> None:
        global _current_toast
        if _current_toast is win:
            _current_toast = None
        try:
            win.destroy()
        except Exception:
            pass

    def _schedule_close() -> None:
        nonlocal close_job
        _cancel_close()
        close_job = win.after(duration_ms, _destroy)

    def _cancel_close() -> None:
        nonlocal close_job
        if close_job is not None:
            try:
                win.after_cancel(close_job)
            except Exception:
                pass
            close_job = None

    def _on_enter(_event=None) -> None:
        _cancel_close()

    def _on_leave(_event=None) -> None:
        _schedule_close()

    def _on_click(_event=None) -> None:
        nonlocal copy_close_job
        try:
            tk_root.clipboard_clear()
            tk_root.clipboard_append(text)
            tk_root.update_idletasks()
        except Exception as exc:
            logger.debug("Clipboard copy failed: %s", exc)
            return
        _cancel_close()
        hint_var.set("copied ✓")
        if copy_close_job is not None:
            try:
                win.after_cancel(copy_close_job)
            except Exception:
                pass
        copy_close_job = win.after(1000, _destroy)

    for widget in widgets:
        widget.bind("<Enter>", _on_enter)
        widget.bind("<Leave>", _on_leave)
        widget.bind("<Button-1>", _on_click)

    # Position bottom-center of the primary screen, ~60px above the
    # taskbar edge, without stealing keyboard focus.
    win.update_idletasks()
    win_width = win.winfo_reqwidth()
    win_height = win.winfo_reqheight()
    screen_width = win.winfo_screenwidth()
    screen_height = win.winfo_screenheight()
    x = (screen_width - win_width) // 2
    y = screen_height - win_height - 90
    win.geometry(f"{win_width}x{win_height}+{x}+{y}")

    win.lift()
    # Deliberately no focus_force()/focus_set()/grab_set(): must not steal
    # keyboard focus from whatever app the user is typing in.

    _schedule_close()


def show_toast_threadsafe(text: str) -> None:
    """Enqueue `text` to be shown as a toast on the main thread. Safe to
    call from any thread.
    """
    _pending.put(text)


def start_poller(tk_root, interval_ms: int = _POLL_INTERVAL_MS) -> None:
    """Start a periodic main-thread poller that drains the pending-toast
    queue and shows toasts. Call once from the main thread after the Tk
    root is created.
    """

    def _poll():
        try:
            while True:
                text = _pending.get_nowait()
                try:
                    show_toast(tk_root, text)
                except Exception as exc:
                    logger.error("Failed to show toast: %s", exc)
        except queue.Empty:
            pass
        finally:
            tk_root.after(interval_ms, _poll)

    tk_root.after(interval_ms, _poll)
