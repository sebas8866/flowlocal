"""History page: the same searchable, day-grouped dictation feed as Home
(via widgets.DictationFeed) without the right-hand stats rail, plus a
"Clear history" button.
"""
from __future__ import annotations

import logging
from typing import Callable, Dict

from flowlocal.ui import theme, widgets

logger = logging.getLogger(__name__)


class HistoryPage:
    def __init__(self, parent, cfg, deps: Dict[str, Callable], app_window) -> None:
        import customtkinter as ctk

        self.cfg = cfg
        self.deps = deps
        self.app_window = app_window
        self._clear_armed = False

        self.frame = ctk.CTkFrame(parent, fg_color=theme.PANEL_BG)
        self.frame.grid_rowconfigure(2, weight=1)
        self.frame.grid_columnconfigure(0, weight=1)

        header_row = ctk.CTkFrame(self.frame, fg_color="transparent")
        header_row.grid(row=0, column=0, sticky="ew", padx=theme.PAD_LG, pady=(theme.PAD_LG, theme.PAD_SM))
        header_row.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header_row, text="History", font=theme.font(20, "bold"), text_color=theme.TEXT
        ).grid(row=0, column=0, sticky="w")

        self._clear_btn = widgets.secondary_button(
            header_row, "Clear history", command=self._on_clear_click
        )
        self._clear_btn.grid(row=0, column=1, sticky="e")

        search_row = ctk.CTkFrame(self.frame, fg_color="transparent")
        search_row.grid(row=1, column=0, sticky="ew", padx=theme.PAD_LG, pady=(0, theme.PAD_SM))
        search_row.grid_columnconfigure(0, weight=1)

        self._search_var = ctk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._feed.set_query(self._search_var.get()))
        search_entry = ctk.CTkEntry(
            search_row,
            textvariable=self._search_var,
            placeholder_text="Search dictations…",
            corner_radius=theme.CORNER_RADIUS_SM,
            border_color=theme.CARD_BORDER,
            fg_color=theme.CARD_BG,
            text_color=theme.TEXT,
            height=34,
        )
        search_entry.grid(row=0, column=0, sticky="ew")

        feed_wrap = ctk.CTkFrame(self.frame, fg_color="transparent")
        feed_wrap.grid(row=2, column=0, sticky="nsew", padx=theme.PAD_LG, pady=(0, theme.PAD_LG))
        feed_wrap.grid_rowconfigure(0, weight=1)
        feed_wrap.grid_columnconfigure(0, weight=1)

        self._feed = widgets.DictationFeed(feed_wrap)
        self._feed.grid(row=0, column=0, sticky="nsew")

    def grid(self, **kwargs):
        self.frame.grid(**kwargs)

    def tkraise(self):
        self.frame.tkraise()

    def on_show(self) -> None:
        self._clear_armed = False
        self._clear_btn.configure(text="Clear history")
        self._load()

    def _load(self) -> None:
        try:
            from flowlocal import history

            entries = history.all()
        except Exception:
            logger.exception("Failed to load history")
            entries = []
        self._feed.set_entries(entries)

    def _on_clear_click(self) -> None:
        if not self._clear_armed:
            self._clear_armed = True
            self._clear_btn.configure(text="Click again to confirm")
            self._clear_btn.after(3000, self._disarm_clear)
            return

        try:
            from flowlocal import history

            history.clear()
        except Exception:
            logger.exception("Failed to clear history")
        self._clear_armed = False
        self._clear_btn.configure(text="Clear history")
        self._load()

    def _disarm_clear(self) -> None:
        if self._clear_armed:
            self._clear_armed = False
            try:
                self._clear_btn.configure(text="Clear history")
            except Exception:
                pass
