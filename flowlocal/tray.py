"""System tray icon via pystray with Pillow-drawn state icons.

pystray/PIL are imported lazily so this module can be imported without
those packages installed.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)

STATE_IDLE = "idle"
STATE_RECORDING = "recording"
STATE_TRANSCRIBING = "transcribing"

_STATE_COLORS = {
    STATE_IDLE: (128, 128, 128, 255),        # gray
    STATE_RECORDING: (220, 40, 40, 255),      # red
    STATE_TRANSCRIBING: (240, 150, 30, 255),  # orange
}

_ICON_SIZE = 32


def _make_icon_image(color):
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (_ICON_SIZE, _ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    margin = 3
    draw.ellipse(
        [margin, margin, _ICON_SIZE - margin, _ICON_SIZE - margin],
        fill=color,
    )
    return image


class Tray:
    """Wraps a pystray Icon with idle/recording/transcribing state icons
    and a Settings/Pause/Quit menu.
    """

    def __init__(
        self,
        on_settings: Optional[Callable[[], None]] = None,
        on_toggle_pause: Optional[Callable[[], None]] = None,
        on_quit: Optional[Callable[[], None]] = None,
    ) -> None:
        self._on_settings = on_settings
        self._on_toggle_pause = on_toggle_pause
        self._on_quit = on_quit
        self._icon = None
        self._state = STATE_IDLE
        self._paused = False
        self._icons_cache = {}

    def _build_icon(self):
        import pystray

        self._icons_cache = {
            state: _make_icon_image(color) for state, color in _STATE_COLORS.items()
        }

        menu = pystray.Menu(
            pystray.MenuItem("Settings", self._handle_settings),
            pystray.MenuItem(
                self._pause_label, self._handle_toggle_pause
            ),
            pystray.MenuItem("Quit", self._handle_quit),
        )

        self._icon = pystray.Icon(
            "FlowLocal",
            icon=self._icons_cache[self._state],
            title="FlowLocal",
            menu=menu,
        )

    def _pause_label(self, item=None) -> str:
        return "Resume" if self._paused else "Pause"

    def _handle_settings(self, icon=None, item=None) -> None:
        if self._on_settings:
            self._on_settings()

    def _handle_toggle_pause(self, icon=None, item=None) -> None:
        self._paused = not self._paused
        if self._on_toggle_pause:
            self._on_toggle_pause()
        self._refresh_menu()

    def _handle_quit(self, icon=None, item=None) -> None:
        if self._on_quit:
            self._on_quit()
        self.stop()

    def _refresh_menu(self) -> None:
        if self._icon is not None:
            self._icon.update_menu()

    def set_state(self, state: str) -> None:
        self._state = state
        if self._icon is not None and state in self._icons_cache:
            self._icon.icon = self._icons_cache[state]

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        self._refresh_menu()

    def notify(self, message: str, title: str = "FlowLocal") -> None:
        if self._icon is not None:
            try:
                self._icon.notify(message, title)
            except Exception as exc:
                logger.debug("Tray notify failed: %s", exc)

    def run(self) -> None:
        """Blocking call — runs the pystray event loop on the calling
        thread.
        """
        if self._icon is None:
            self._build_icon()
        self._icon.run()

    def run_detached(self) -> None:
        """Non-blocking: runs the tray icon loop on a background thread."""
        if self._icon is None:
            self._build_icon()
        self._icon.run_detached()

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception as exc:
                logger.debug("Tray stop failed: %s", exc)
