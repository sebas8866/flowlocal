"""Text injection via clipboard save/set/paste/restore.

pywin32 and pynput are imported lazily so this module can be imported
without those packages present.
"""
from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)

_RESTORE_DELAY_SECONDS = 0.4
_CLIPBOARD_RETRY_ATTEMPTS = 10
_CLIPBOARD_RETRY_DELAY_SECONDS = 0.05


class InjectionFallback(Exception):
    """Raised when injection fails.

    `text_on_clipboard` distinguishes the two failure modes so callers can
    give an honest message:
    - True: the dictated text is on the clipboard (clipboard set succeeded,
      only the paste keystroke failed) — the user can paste it manually.
    - False: we could not even set the clipboard, so the dictated text is
      NOT available there.
    """

    def __init__(self, message: str, text_on_clipboard: bool) -> None:
        super().__init__(message)
        self.text_on_clipboard = text_on_clipboard


def _open_clipboard_with_retry():
    import win32clipboard

    last_exc = None
    for _ in range(_CLIPBOARD_RETRY_ATTEMPTS):
        try:
            win32clipboard.OpenClipboard()
            return
        except Exception as exc:
            last_exc = exc
            time.sleep(_CLIPBOARD_RETRY_DELAY_SECONDS)
    raise RuntimeError("Could not open clipboard") from last_exc


def _get_clipboard_text():
    import win32clipboard
    import win32con

    try:
        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
    except Exception as exc:
        logger.debug("Could not read existing clipboard text: %s", exc)
    return None


def _set_clipboard_text(text: str) -> None:
    import win32clipboard
    import win32con

    win32clipboard.EmptyClipboard()
    win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)


def _restore_clipboard_later(saved_text, text_we_set: str) -> None:
    """Restore `saved_text` onto the clipboard after a short delay, but
    only if the clipboard still holds exactly the text we placed there
    (`text_we_set`). If something else wrote to the clipboard in the
    meantime, leave it alone rather than clobbering the newer content.
    """
    def _run():
        time.sleep(_RESTORE_DELAY_SECONDS)
        try:
            _open_clipboard_with_retry()
            try:
                current = _get_clipboard_text()
                if current != text_we_set:
                    return
                win_clip_module = __import__("win32clipboard")
                win_clip_module.EmptyClipboard()
                if saved_text is not None:
                    import win32con

                    win_clip_module.SetClipboardData(win32con.CF_UNICODETEXT, saved_text)
            finally:
                __import__("win32clipboard").CloseClipboard()
        except Exception as exc:
            logger.warning("Failed to restore clipboard: %s", exc)

    threading.Thread(target=_run, daemon=True).start()


def _send_paste() -> None:
    from pynput.keyboard import Controller, Key

    kb = Controller()
    time.sleep(0.05)
    kb.press(Key.ctrl)
    time.sleep(0.02)
    kb.press("v")
    time.sleep(0.02)
    kb.release("v")
    time.sleep(0.02)
    kb.release(Key.ctrl)
    time.sleep(0.05)


def inject(text: str) -> None:
    """Save the current clipboard, place `text` on it, send Ctrl+V, then
    restore the original clipboard content after a short delay.

    No-ops on empty text. Raises InjectionFallback if paste fails; the
    dictated text remains on the clipboard in that case.
    """
    if not text or not text.strip():
        return

    import win32clipboard

    saved_text = None
    try:
        _open_clipboard_with_retry()
        try:
            saved_text = _get_clipboard_text()
            _set_clipboard_text(text)
        finally:
            win32clipboard.CloseClipboard()
    except Exception as exc:
        logger.error("Failed to set clipboard for injection: %s", exc)
        raise InjectionFallback(
            f"Could not set clipboard: {exc}", text_on_clipboard=False
        ) from exc

    try:
        _send_paste()
    except Exception as exc:
        logger.error("Failed to send paste keystroke: %s", exc)
        raise InjectionFallback(
            f"Paste failed, text left on clipboard: {exc}", text_on_clipboard=True
        ) from exc

    _restore_clipboard_later(saved_text, text)
