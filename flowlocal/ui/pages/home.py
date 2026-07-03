"""Home page: the dictation feed (today's history, grouped by day) plus a
right-rail stats sidebar, styled after the real Wispr Flow home screen.
"""
from __future__ import annotations

import logging
from typing import Callable, Dict

from flowlocal.ui import theme, widgets

logger = logging.getLogger(__name__)

_RIGHT_RAIL_W = 230


def _welcome_name() -> str:
    """Windows username, capitalized, for the 'Welcome back, {name}'
    header. Falls back to a plain greeting on any failure (e.g. getpass
    can't resolve a username in some sandboxed/headless environments).
    """
    try:
        import getpass

        name = getpass.getuser()
        if name:
            return name.capitalize()
    except Exception:
        logger.exception("Failed to resolve Windows username for greeting")
    return ""


class HomePage:
    def __init__(self, parent, cfg, deps: Dict[str, Callable], app_window) -> None:
        import customtkinter as ctk

        self.cfg = cfg
        self.deps = deps
        self.app_window = app_window
        self._search_visible = False

        self.frame = ctk.CTkFrame(parent, fg_color=theme.PANEL_BG)
        self.frame.grid_rowconfigure(0, weight=1)
        self.frame.grid_columnconfigure(0, weight=1)

        # --- left: feed column ---------------------------------------------
        feed_col = ctk.CTkFrame(self.frame, fg_color="transparent")
        feed_col.grid(row=0, column=0, sticky="nsew")
        feed_col.grid_rowconfigure(2, weight=1)
        feed_col.grid_columnconfigure(0, weight=1)

        name = _welcome_name()
        header_text = f"Welcome back, {name}" if name else "Welcome back"
        ctk.CTkLabel(
            feed_col, text=header_text, font=theme.font(20, "bold"), text_color=theme.TEXT, anchor="w"
        ).grid(row=0, column=0, sticky="w", padx=theme.PAD_LG, pady=(theme.PAD_LG, theme.PAD_SM))

        section_row = ctk.CTkFrame(feed_col, fg_color="transparent")
        section_row.grid(row=1, column=0, sticky="ew", padx=theme.PAD_LG, pady=(0, theme.PAD_XS))
        section_row.grid_columnconfigure(0, weight=1)

        widgets.secondary_label(section_row, "TODAY").grid(row=0, column=0, sticky="w")
        self._search_toggle_btn = widgets.icon_button(
            section_row, "🔍", command=self._on_toggle_search
        )
        self._search_toggle_btn.grid(row=0, column=1, sticky="e")

        self._search_var = ctk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._feed.set_query(self._search_var.get()))
        self._search_entry = ctk.CTkEntry(
            section_row,
            textvariable=self._search_var,
            placeholder_text="Search dictations…",
            corner_radius=theme.CORNER_RADIUS_SM,
            border_color=theme.CARD_BORDER,
            fg_color=theme.CARD_BG,
            text_color=theme.TEXT,
            height=28,
        )
        # Gridded on demand in _on_toggle_search; not shown by default.

        feed_wrap = ctk.CTkFrame(feed_col, fg_color="transparent")
        feed_wrap.grid(row=2, column=0, sticky="nsew", padx=theme.PAD_LG, pady=(0, theme.PAD_LG))
        feed_wrap.grid_rowconfigure(0, weight=1)
        feed_wrap.grid_columnconfigure(0, weight=1)

        self._feed = widgets.DictationFeed(feed_wrap, on_change=self._on_feed_change)
        self._feed.grid(row=0, column=0, sticky="nsew")

        # --- right: stats rail -----------------------------------------------
        rail = ctk.CTkFrame(self.frame, fg_color="transparent", width=_RIGHT_RAIL_W)
        rail.grid(row=0, column=1, sticky="ns", padx=(0, theme.PAD_LG), pady=theme.PAD_LG)
        rail.grid_propagate(False)

        stats_card, stats_inner = widgets.make_card(rail)
        stats_card.pack(fill="x", pady=(0, theme.PAD_MD))
        self._stat_rows = {}
        for key, label in (("total_words", "total words"), ("avg_wpm", "wpm"), ("streak_days", "day streak")):
            row = ctk.CTkFrame(stats_inner, fg_color="transparent")
            row.pack(fill="x", pady=6, anchor="w")
            value_label = ctk.CTkLabel(
                row, text="0", font=theme.serif_font(26), text_color=theme.TEXT, anchor="w"
            )
            value_label.pack(side="left")
            widgets.secondary_label(row, f"  {label}").pack(side="left", pady=(10, 0))
            self._stat_rows[key] = value_label

        today_card, today_inner = widgets.make_card(rail)
        today_card.pack(fill="x")
        widgets.card_header(today_inner, "Today").pack(anchor="w", pady=(0, theme.PAD_SM))
        today_row = ctk.CTkFrame(today_inner, fg_color="transparent")
        today_row.pack(fill="x", anchor="w")
        self._words_today_label = ctk.CTkLabel(
            today_row, text="0", font=theme.serif_font(26), text_color=theme.TEXT, anchor="w"
        )
        self._words_today_label.pack(side="left")
        widgets.secondary_label(today_row, "  words today").pack(side="left", pady=(10, 0))

    def grid(self, **kwargs):
        self.frame.grid(**kwargs)

    def tkraise(self):
        self.frame.tkraise()

    def on_show(self) -> None:
        self._refresh_stats()
        self._load_feed()

    def _on_toggle_search(self) -> None:
        self._search_visible = not self._search_visible
        if self._search_visible:
            self._search_entry.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(theme.PAD_XS, 0))
        else:
            self._search_entry.grid_forget()
            self._search_var.set("")

    def _refresh_stats(self) -> None:
        try:
            from flowlocal import history

            s = history.stats()
        except Exception:
            logger.exception("Failed to compute history stats")
            s = {"words_today": 0, "total_words": 0, "avg_wpm": 0.0, "streak_days": 0}

        self._stat_rows["total_words"].configure(text=theme.format_count(s.get("total_words", 0)))
        self._stat_rows["avg_wpm"].configure(text=f"{s.get('avg_wpm', 0.0):.0f}")
        self._stat_rows["streak_days"].configure(text=str(s.get("streak_days", 0)))
        self._words_today_label.configure(text=theme.format_count(s.get("words_today", 0)))

    def _load_feed(self) -> None:
        try:
            from flowlocal import history

            entries = history.all()
        except Exception:
            logger.exception("Failed to load history")
            entries = []
        self._feed.set_entries(entries)

    def _on_feed_change(self) -> None:
        self._refresh_stats()
