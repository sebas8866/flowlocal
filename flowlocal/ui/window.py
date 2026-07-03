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
_SIDEBAR_W = 200
_PANEL_INSET = 12

# Nav items shown in the sidebar's main list. Settings is intentionally
# excluded here — it lives in the bottom links row instead (see
# _build_sidebar), matching the real Wispr Flow layout.
_NAV_ITEMS = [
    ("home", "⌂", "Home"),
    ("history", "⏱", "History"),
    ("dictionary", "✎", "Dictionary"),
]

_HELP_URL = "https://github.com/sebas8866/flowlocal"

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
        # Spacer row sits right after the nav list (row 0 = wordmark, rows
        # 1..len(_NAV_ITEMS) = nav items) and pushes the promo card +
        # footer down to the bottom of the sidebar.
        _spacer_row = len(_NAV_ITEMS) + 1
        sidebar.grid_rowconfigure(_spacer_row, weight=1)
        self.sidebar = sidebar

        # --- wordmark row: waveform glyph + name + backend badge ---------
        wordmark = ctk.CTkFrame(sidebar, fg_color="transparent")
        wordmark.grid(row=0, column=0, sticky="ew", padx=theme.PAD_MD, pady=(theme.PAD_LG, theme.PAD_MD))
        ctk.CTkLabel(
            wordmark, text="❙❙❙", text_color=theme.ACCENT, font=theme.font(11), width=16
        ).pack(side="left")
        ctk.CTkLabel(
            wordmark, text="FlowLocal", font=theme.font(16, "bold"), text_color=theme.TEXT
        ).pack(side="left", padx=(4, 0))

        self._backend_badge = ctk.CTkLabel(
            wordmark,
            text="Local",
            font=theme.font(10, "bold"),
            text_color=theme.TEXT_SECONDARY,
            fg_color="transparent",
            corner_radius=theme.CORNER_RADIUS_SM,
            height=20,
            width=52,
            border_width=1,
        )
        self._backend_badge.pack(side="right")
        self._style_backend_badge()

        # --- nav list ------------------------------------------------------
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

        # Row _spacer_row is the flexible empty spacer (grid_rowconfigure
        # above); the promo card and footer sit in the two rows after it.
        promo_row = _spacer_row + 1
        footer_row = promo_row + 1

        self._build_promo_card(sidebar, promo_row)
        self._build_footer(sidebar, footer_row)

    def _style_backend_badge(self) -> None:
        backend = getattr(self.cfg, "backend", "local")
        label = "Cloud" if backend == "cloud" else "Local"
        self._backend_badge.configure(text=label, border_color=theme.CARD_BORDER)

    def _build_promo_card(self, sidebar, row: int) -> None:
        import customtkinter as ctk

        card = ctk.CTkFrame(sidebar, corner_radius=theme.CORNER_RADIUS, fg_color=theme.PROMO_BG)
        card.grid(row=row, column=0, sticky="ew", padx=theme.PAD_MD, pady=(0, theme.PAD_SM))
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=theme.PAD_SM, pady=theme.PAD_SM)

        ctk.CTkLabel(
            inner, text="Unlimited dictation", font=theme.font(12, "bold"), text_color=theme.TEXT,
            anchor="w", justify="left",
        ).pack(anchor="w")

        self._promo_body_label = ctk.CTkLabel(
            inner,
            text="",
            font=theme.font(10),
            text_color=theme.TEXT_SECONDARY,
            anchor="w",
            justify="left",
            wraplength=_SIDEBAR_W - (2 * theme.PAD_MD) - (2 * theme.PAD_SM),
        )
        self._promo_body_label.pack(anchor="w", pady=(4, 0))
        self._refresh_promo_card()

    def _refresh_promo_card(self) -> None:
        backend = getattr(self.cfg, "backend", "local")
        if backend == "cloud":
            text = "Powered by Groq cloud — still free, still unlimited."
        else:
            text = "No word limits, no subscription. Everything runs on your machine."
        self._promo_body_label.configure(text=text)

    def _build_footer(self, sidebar, row: int) -> None:
        import customtkinter as ctk

        footer = ctk.CTkFrame(sidebar, fg_color="transparent")
        footer.grid(row=row, column=0, sticky="ews", padx=theme.PAD_MD, pady=(0, theme.PAD_MD))

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

        dark_switch = ctk.CTkSwitch(
            status_row,
            text="",
            width=32,
            command=self._on_theme_toggle,
            progress_color=theme.ACCENT,
        )
        if self.cfg.theme == "dark":
            dark_switch.select()
        dark_switch.pack(side="right")
        self._dark_switch = dark_switch

        settings_link = ctk.CTkButton(
            footer,
            text="⚙  Settings",
            anchor="w",
            corner_radius=theme.CORNER_RADIUS_SM,
            fg_color="transparent",
            hover_color=theme.NAV_HOVER_BG,
            text_color=theme.TEXT_SECONDARY,
            font=theme.font(12),
            height=28,
            command=lambda: self.show_page("settings"),
        )
        settings_link.pack(fill="x", pady=(4, 0))
        self._nav_buttons["settings"] = settings_link

        help_link = ctk.CTkButton(
            footer,
            text="?  Help",
            anchor="w",
            corner_radius=theme.CORNER_RADIUS_SM,
            fg_color="transparent",
            hover_color=theme.NAV_HOVER_BG,
            text_color=theme.TEXT_SECONDARY,
            font=theme.font(12),
            height=28,
            command=self._on_help_click,
        )
        help_link.pack(fill="x", pady=(2, 0))

        from flowlocal import __version__

        ctk.CTkLabel(
            footer, text=f"v{__version__}", font=theme.font(10), text_color=theme.TEXT_SECONDARY
        ).pack(anchor="w", pady=(6, 0))

    @staticmethod
    def _on_help_click() -> None:
        import webbrowser

        try:
            webbrowser.open(_HELP_URL)
        except Exception:
            logger.exception("Failed to open help URL")

    def _build_page_area(self) -> None:
        import customtkinter as ctk

        # Outer wrapper keeps the beige background and provides the inset
        # that makes the white panel "float" off the top/right/bottom
        # edges, per the real Wispr Flow layout.
        outer = ctk.CTkFrame(self.win, fg_color=theme.BG, corner_radius=0)
        outer.grid(row=0, column=1, sticky="nsew")
        outer.grid_rowconfigure(0, weight=1)
        outer.grid_columnconfigure(0, weight=1)

        panel = ctk.CTkFrame(outer, fg_color=theme.PANEL_BG, corner_radius=theme.CORNER_RADIUS_LG)
        panel.grid(
            row=0, column=0, sticky="nsew",
            padx=(0, _PANEL_INSET), pady=(_PANEL_INSET, _PANEL_INSET),
        )
        panel.grid_rowconfigure(0, weight=1)
        panel.grid_columnconfigure(0, weight=1)

        self.page_area = panel

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

        self._style_backend_badge()
        self._refresh_promo_card()

    # --- close ---------------------------------------------------------------

    def _on_close(self) -> None:
        global _current_window
        _current_window = None
        try:
            self.win.destroy()
        except Exception:
            pass
