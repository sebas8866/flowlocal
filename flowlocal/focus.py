"""Detect whether the currently focused control is a text input.

Used to decide whether it is safe to show an intrusive UI (e.g. auto-expand
the result popup) without interrupting active typing/dictation targets.

THREADING MODEL: called from the single pipeline worker thread only (see
flowlocal/app.py). comtypes handles COM initialization for us on import /
first use; we still guard with try/except since CoInitialize semantics can
vary depending on what else touched COM on this thread.

Must be fast (<50ms typical) and must never raise — any failure degrades to
a safe fallback.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

_UIA_VALUE_PATTERN_ID = 10002
_UIA_TEXT_PATTERN_ID = 10014
_UIA_IS_VALUE_PATTERN_AVAILABLE = 30043
_UIA_IS_TEXT_PATTERN_AVAILABLE = 30038
_UIA_VALUE_IS_READONLY = 30046

# COM objects are apartment-threaded; a CUIAutomation instance created on
# one thread must not be used from another. Cache per-thread instead of
# globally so e.g. a warmup thread's object is never handed to the worker
# thread.
_uia_local = threading.local()
_logged_uia_failure = False
_logged_fallback_failure = False


def _get_uia():
    """Return this thread's cached IUIAutomation COM instance, creating it
    on first call from this thread. Returns None if UI Automation is
    unavailable.
    """
    instance = getattr(_uia_local, "instance", None)
    if instance is not None:
        return instance
    if getattr(_uia_local, "init_failed", False):
        return None

    try:
        import comtypes.client

        try:
            from comtypes.gen.UIAutomationClient import CUIAutomation, IUIAutomation
        except ImportError:
            # First use: generate the wrapper module from the type library.
            comtypes.client.GetModule("UIAutomationCore.dll")
            from comtypes.gen.UIAutomationClient import CUIAutomation, IUIAutomation
    except Exception as exc:
        _uia_local.init_failed = True
        _log_uia_failure(exc)
        return None

    try:
        instance = comtypes.client.CreateObject(
            CUIAutomation, interface=IUIAutomation
        )
        _uia_local.instance = instance
    except Exception as exc:
        _uia_local.init_failed = True
        _log_uia_failure(exc)
        return None

    return instance


def prewarm() -> None:
    """Pay the one-time comtypes codegen cost (~0.5s) for the UIA type
    library, without creating or caching a COM object on the calling
    thread. Safe to call from any thread (e.g. an app warmup thread) since
    COM objects themselves are per-thread and must not be shared across
    threads; codegen (module generation) is a process-global, thread-safe
    step. The real IUIAutomation object is created lazily, per-thread, on
    first real use via _get_uia().
    """
    try:
        import comtypes.client

        try:
            from comtypes.gen.UIAutomationClient import CUIAutomation, IUIAutomation  # noqa: F401
        except ImportError:
            comtypes.client.GetModule("UIAutomationCore.dll")
    except Exception as exc:
        logger.debug("UIA prewarm (codegen) failed: %s", exc)


def _log_uia_failure(exc: Exception) -> None:
    global _logged_uia_failure
    if not _logged_uia_failure:
        logger.debug("UI Automation unavailable, will use fallback: %s", exc)
        _logged_uia_failure = True


def _log_fallback_failure(exc: Exception) -> None:
    global _logged_fallback_failure
    if not _logged_fallback_failure:
        logger.debug("Focus fallback check failed: %s", exc)
        _logged_fallback_failure = True


def _check_via_uia() -> Optional[bool]:
    """Return True/False if UI Automation could determine focus state,
    or None if UIA itself is unavailable/errored (caller should fall back).
    """
    uia = _get_uia()
    if uia is None:
        return None

    try:
        element = uia.GetFocusedElement()
        if element is None:
            return None

        has_value = bool(
            element.GetCurrentPropertyValue(_UIA_IS_VALUE_PATTERN_AVAILABLE)
        )
        has_text = bool(
            element.GetCurrentPropertyValue(_UIA_IS_TEXT_PATTERN_AVAILABLE)
        )

        if has_value:
            read_only = bool(
                element.GetCurrentPropertyValue(_UIA_VALUE_IS_READONLY)
            )
            if not read_only:
                return True
            # ValuePattern is read-only; TextPattern alone still counts.
            return True if has_text else False

        if has_text:
            return True

        return False
    except Exception as exc:
        _log_uia_failure(exc)
        return None


def _check_via_fallback() -> bool:
    """win32gui/win32process fallback: True if the foreground thread has a
    caret window. Suppresses popups (returns True) as the least-annoying
    default if this also fails.
    """
    try:
        import win32gui
        import win32process

        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return True

        tid, _pid = win32process.GetWindowThreadProcessId(hwnd)
        gui_info = win32gui.GetGUIThreadInfo(tid)
        # GetGUIThreadInfo returns a tuple; hwndCaret is index 4.
        hwnd_caret = gui_info[4]
        return bool(hwnd_caret)
    except Exception as exc:
        _log_fallback_failure(exc)
        return True


def is_text_input_focused() -> bool:
    """Best-effort check: is the currently focused UI control a text input?

    Tries UI Automation first, falls back to a caret-presence heuristic via
    win32gui, and finally defaults to True (suppressing popups is the
    least-annoying default) if everything fails. Never raises.
    """
    try:
        result = _check_via_uia()
        if result is not None:
            return result
    except Exception as exc:
        _log_uia_failure(exc)

    try:
        return _check_via_fallback()
    except Exception as exc:
        _log_fallback_failure(exc)
        return True
