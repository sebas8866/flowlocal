"""App-aware context: identify the foreground app + window title so the
stage-2 LLM rewrite can match the target app's natural style.

All win32 imports are lazy inside the function so this module can be
imported (and, on non-Windows or a bare interpreter, safely no-op) without
pywin32 installed.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_TITLE_MAX_CHARS = 80


def get_app_context() -> "str | None":
    """Return a short string like "Discord.exe — #general | Discord"
    describing the current foreground window, or None on any failure
    (missing pywin32, no foreground window, access denied, etc.).
    """
    try:
        import os

        import win32api
        import win32gui
        import win32process

        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None

        title = win32gui.GetWindowText(hwnd) or ""
        title = title[:_TITLE_MAX_CHARS]

        exe_name = None
        try:
            _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = win32api.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            try:
                exe_path = win32process.GetModuleFileNameEx(handle, 0)
                exe_name = os.path.basename(exe_path)
            finally:
                win32api.CloseHandle(handle)
        except Exception as exc:
            logger.debug("Could not resolve foreground process exe: %s", exc)
            exe_name = None

        if exe_name and title:
            return f"{exe_name} — {title}"
        if exe_name:
            return exe_name
        if title:
            return title
        return None
    except Exception as exc:
        logger.debug("get_app_context failed: %s", exc)
        return None
