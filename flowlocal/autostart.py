"""Windows autostart management via HKCU Run registry key.

`winreg` is stdlib but Windows-only; imported lazily so this module can be
imported (and safely no-op) on non-Windows interpreters.
"""
from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger(__name__)

_RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "FlowLocal"


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _default_command() -> str:
    root = _project_root()
    pythonw = os.path.join(root, ".venv", "Scripts", "pythonw.exe")
    launcher = os.path.join(root, "run_flowlocal.pyw")
    return f'"{pythonw}" "{launcher}"'


def enable() -> None:
    """Add/overwrite the HKCU Run entry pointing at the venv's pythonw.exe
    and run_flowlocal.pyw.
    """
    import winreg

    command = _default_command()
    key = winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, _RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE
    )
    try:
        winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_SZ, command)
    finally:
        winreg.CloseKey(key)


def disable() -> None:
    """Remove the HKCU Run entry, if present."""
    import winreg

    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE
        )
        try:
            winreg.DeleteValue(key, _VALUE_NAME)
        finally:
            winreg.CloseKey(key)
    except FileNotFoundError:
        pass


def is_enabled() -> bool:
    """True if the HKCU Run entry exists and matches our expected command."""
    import winreg

    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_KEY_PATH, 0, winreg.KEY_READ
        )
        try:
            value, _type = winreg.QueryValueEx(key, _VALUE_NAME)
            return bool(value)
        finally:
            winreg.CloseKey(key)
    except FileNotFoundError:
        return False
    except OSError as exc:
        logger.debug("Could not read autostart registry key: %s", exc)
        return False
