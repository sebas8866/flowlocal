"""FlowLocal app window package (CustomTkinter).

Public entrypoint: `open_window(root, cfg, deps, page="home")`. See
flowlocal/ui/window.py for the threading model and singleton contract
(matches the old flowlocal/settings_ui.py that this package replaces).
"""
from __future__ import annotations

from flowlocal.ui.window import open_window

__all__ = ["open_window"]
