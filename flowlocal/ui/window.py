"""The FlowLocal app window: sidebar navigation + page switcher.

THREADING MODEL (must match flowlocal/app.py):
tkinter's Tcl interpreter is not thread-safe and must only be touched on
the process's MAIN thread. This module's `open_window()` does NOT create
its own Tk root or mainloop — it is called on the main thread (directly,
or marshalled via `root.after(0, ...)` if triggered from a background
thread such as the tray menu callback) and creates a CTkToplevel attached
to the shared hidden root.

Only one app window may exist at a time; calling `open_window()` again
while one is open focuses the existing window and switches its page
instead of creating a new one.
"""
from __future__ import annotations

import logging
from typing import Callable, Dict, Optional

from flowlocal.ui import theme

logger = logging.getLogger(__name__)

_WINDOW_W = 920
_WINDOW_H = 640
_MIN_W = 760
_MIN_H = 560
_SIDEBAR_W = 210

_NAV_ITEMS = [
    ("home", "⌂", "Home"),
    ("history", "⏱", "History"),
    ("dictionary", "✎", "Dictionary"),
    ("settings", "⚙", "Settings"),
]

_current_window = None  # module-level singleton guard


def open_window(root, cfg, deps: Dict[str, Callable], page: str = "home") -> None:
    """Open (or focus + switch) the app window.

    `root` is the shared hidden Tk root running the main-thread mainloop.
    `cfg` is the live Config instance.
    `deps` is the callback dict (see flowlocal/app.py `_open_settings` for
    the full contract — this window forwards the same keys to its pages).
    `page` selects which page is shown initially / on refocus.
    """
    import customtkinter as ctk

    global _current_window

    if _current_window is not None and _current_window.winfo_exists():
        _current_window.show_page(page)
        _current_window.deiconify()
        _current_window.lift()
        _current_window.focus_force()
        return

    ctk.set_default_color_theme("blue")  # base preset; we override per-widget colors
    win = _AppWindow(root, cfg, deps, initial_page=page)
    _current_window = win
    win.deiconify()
    win.lift()
    win.focus_force()


class _AppWindow:
    """Thin wrapper around a CTkToplevel that owns the sidebar + page area.

    Not a CTkToplevel subclass itself so we can keep constructor ordering
    simple (build chrome, then pages, then show initial page) without
    fighting CTk's own __init__ chain.
    """

    def __init__(self, root, cfg, deps: Dict[str, Callable], initial_page: str) -> None:
        import customtkinter as ctk

        self.cfg = cfg
        self.deps = deps
        self._pages: Dict[str, object] = {}
        self._nav_buttons: Dict[str, "ctk.CTkButton"] = {}
        self._current_page: Optional[str] = None

        appearance = cfg.theme if cfg.theme in ("light", "dark", "system") else "light"
        ctk.set_appearance_mode(appearance)

        self.win = ctk.CTkToplevel(root)
        win = self.win
        win.title("FlowLocal")
        win.geometry(f"{_WINDOW_W}x{_WINDOW_H}")
        win.minsize(_MIN_W, _MIN_H)
        win.configure(fg_color=theme.BG)

        win.protocol("WM_DELETE_WINDOW", self._on_close)

        win.grid_columnconfigure(1, weight=1)
        win.grid_rowconfigure(0, weight=1)

        self._build_sidebar()
        self._build_page_area()
        self._build_pages()

        self.show_page(initial_page)

        self._refresh_status_loop()

    # --- delegate winfo/deiconify/lift/focus_force to the CTkToplevel -----

    def winfo_exists(self):
        return self.win.winfo_exists()

    def deiconify(self):
        self.win.deiconify()

    def lift(self):
        self.win.lift()

    def focus_force(self):
        self.win.focus_force()

    # --- chrome ----------------------------------------------------------

    def _build_sidebar(self) -> None:
        import customtkinter as ctk

        sidebar = ctk.CTkFrame(
            self.win, width=_SIDEBAR_W, corner_radius=0, fg_color=theme.SIDEBAR_BG
        )
        sidebar.grid(row=0, column=0, sticky="nsw")
        sidebar.grid_propagate(False)
        sidebar.grid_rowconfigure(5, weight=1)  # spacer pushes footer down
        self.sidebar = sidebar

        wordmark = ctk.CTkFrame(sidebar, fg_color="transparent")
        wordmark.grid(row=0, column=0, sticky="ew", padx=theme.PAD_MD, pady=(theme.PAD_LG, theme.PAD_MD))
        dot = ctk.CTkLabel(
            wordmark, text="●", text_color=theme.ACCENT, font=theme.font(12), width=16
        )
        dot.pack(side="left")
        ctk.CTkLabel(
            wordmark, text="FlowLocal", font=theme.font(16, "bold"), text_color=theme.TEXT
        ).pack(side="left", padx=(4, 0))

        for i, (key, icon, label) in enumerate(_NAV_ITEMS, start=1):
            btn = ctk.CTkButton(
                sidebar,
                text=f"{icon}  {label}",
                anchor="w",
                corner_radius=theme.CORNER_RADIUS_SM,
                fg_color="transparent",
                hover_color=theme.NAV_HOVER_BG,
                text_color=theme.TEXT,
                font=theme.font(13),
                height=36,
                command=lambda k=key: self.show_page(k),
            )
            btn.grid(row=i, column=0, sticky="ew", padx=theme.PAD_SM, pady=2)
            self._nav_buttons[key] = btn

        footer = ctk.CTkFrame(sidebar, fg_color="transparent")
        footer.grid(row=6, column=0, sticky="ews", padx=theme.PAD_MD, pady=(0, theme.PAD_MD))

        status_row = ctk.CTkFrame(footer, fg_color="transparent")
        status_row.pack(fill="x", pady=(0, 8))
        self._status_dot = ctk.CTkLabel(
            status_row, text="●", font=theme.font(10), text_color=theme.SUCCESS, width=14
        )
        self._status_dot.pack(side="left")
        self._status_label = ctk.CTkLabel(
            status_row, text="Listening", font=theme.font(11), text_color=theme.TEXT_SECONDARY
        )
        self._status_label.pack(side="left", padx=(4, 0))

        theme_row = ctk.CTkFrame(footer, fg_color="transparent")
        theme_row.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(
            theme_row, text="Dark mode", font=theme.font(11), text_color=theme.TEXT_SECONDARY
        ).pack(side="left")
        self._dark_switch = ctk.CTkSwitch(
            theme_row,
            text="",
            width=36,
            command=self._on_theme_toggle,
            progress_color=theme.ACCENT,
        )
        if self.cfg.theme == "dark":
            self._dark_switch.select()
        self._dark_switch.pack(side="right")

        from flowlocal import __version__

        ctk.CTkLabel(
            footer, text=f"v{__version__}", font=theme.font(10), text_color=theme.TEXT_SECONDARY
        ).pack(anchor="w")

    def _build_page_area(self) -> None:
        import customtkinter as ctk

        self.page_area = ctk.CTkFrame(self.win, fg_color=theme.BG, corner_radius=0)
        self.page_area.grid(row=0, column=1, sticky="nsew")
        self.page_area.grid_rowconfigure(0, weight=1)
        self.page_area.grid_columnconfigure(0, weight=1)

    def _build_pages(self) -> None:
        from flowlocal.ui.pages import dictionary as dictionary_page
        from flowlocal.ui.pages import history as history_page
        from flowlocal.ui.pages import home as home_page
        from flowlocal.ui.pages import settings as settings_page

        self._pages["home"] = home_page.HomePage(self.page_area, self.cfg, self.deps, self)
        self._pages["history"] = history_page.HistoryPage(self.page_area, self.cfg, self.deps, self)
        self._pages["dictionary"] = dictionary_page.DictionaryPage(self.page_area, self.cfg, self.deps, self)
        self._pages["settings"] = settings_page.SettingsPage(self.page_area, self.cfg, self.deps, self)

        for page in self._pages.values():
            page.grid(row=0, column=0, sticky="nsew")

    # --- navigation --------------------------------------------------------

    def show_page(self, key: str) -> None:
        if key not in self._pages:
            key = "home"

        for nav_key, btn in self._nav_buttons.items():
            if nav_key == key:
                btn.configure(fg_color=theme.NAV_ACTIVE_BG)
            else:
                btn.configure(fg_color="transparent")

        page = self._pages[key]
        page.tkraise()
        refresh = getattr(page, "on_show", None)
        if refresh:
            try:
                refresh()
            except Exception:
                logger.exception("Error refreshing page %r", key)

        self._current_page = key

    # --- theme toggle --------------------------------------------------------

    def _on_theme_toggle(self) -> None:
        import customtkinter as ctk

        new_theme = "dark" if self._dark_switch.get() else "light"
        ctk.set_appearance_mode(new_theme)
        self.cfg.theme = new_theme
        on_theme_change = self.deps.get("on_theme_change")
        if on_theme_change:
            try:
                on_theme_change(new_theme)
            except Exception:
                logger.exception("on_theme_change callback failed")

    # --- pause status footer ------------------------------------------------

    def _refresh_status_loop(self) -> None:
        self._refresh_status()
        try:
            self.win.after(1000, self._refresh_status_loop)
        except Exception:
            pass

    def _refresh_status(self) -> None:
        is_paused = self.deps.get("is_paused")
        paused = bool(is_paused()) if is_paused else False
        if paused:
            self._status_label.configure(text="Paused")
            self._status_dot.configure(text_color=theme.TEXT_SECONDARY)
        else:
            self._status_label.configure(text="Listening")
            self._status_dot.configure(text_color=theme.SUCCESS)

    # --- close ---------------------------------------------------------------

    def _on_close(self) -> None:
        global _current_window
        _current_window = None
        try:
            self.win.destroy()
        except Exception:
            pass
