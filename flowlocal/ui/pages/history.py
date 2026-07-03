"""History page: searchable, scrollable list of all stored dictations."""
from __future__ import annotations

import logging
import time
from typing import Callable, Dict

from flowlocal.ui import theme, widgets

logger = logging.getLogger(__name__)


class HistoryPage:
    def __init__(self, parent, cfg, deps: Dict[str, Callable], app_window) -> None:
        import customtkinter as ctk

        self.cfg = cfg
        self.deps = deps
        self.app_window = app_window
        self._entries_cache = []
        self._clear_armed = False

        self.frame = ctk.CTkFrame(parent, fg_color=theme.BG)
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
        self._search_var.trace_add("write", lambda *_: self._render_list())
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

        self._scroll = ctk.CTkScrollableFrame(self.frame, fg_color="transparent")
        self._scroll.grid(row=2, column=0, sticky="nsew", padx=theme.PAD_LG, pady=(0, theme.PAD_LG))
        self._scroll.grid_columnconfigure(0, weight=1)

        self._empty_label = widgets.secondary_label(self._scroll, "No dictations yet.")

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

            self._entries_cache = history.all()
        except Exception:
            logger.exception("Failed to load history")
            self._entries_cache = []
        self._render_list()

    def _render_list(self) -> None:
        for child in self._scroll.winfo_children():
            if child is not self._empty_label:
                child.destroy()

        query = self._search_var.get().strip().lower()
        entries = self._entries_cache
        if query:
            entries = [e for e in entries if query in e.get("text", "").lower()]

        if not entries:
            self._empty_label.configure(
                text="No matches." if query else "No dictations yet."
            )
            self._empty_label.pack(anchor="w", pady=theme.PAD_SM)
            return
        self._empty_label.pack_forget()

        import customtkinter as ctk

        now = time.time()
        for entry in entries:
            card, inner = widgets.make_card(self._scroll)
            card.pack(fill="x", pady=4)

            top_row = ctk.CTkFrame(inner, fg_color="transparent")
            top_row.pack(fill="x")
            top_row.grid_columnconfigure(0, weight=1)

            widgets.secondary_label(
                top_row, widgets.relative_time(entry.get("ts", now), now)
            ).grid(row=0, column=0, sticky="w")

            text = entry.get("text", "")
            copy_btn = widgets.icon_button(
                top_row, "📋", command=lambda t=text: widgets.copy_to_clipboard(self.frame, t)
            )
            copy_btn.grid(row=0, column=1, sticky="e")

            body_label = ctk.CTkLabel(
                inner,
                text=text,
                font=theme.font(12),
                text_color=theme.TEXT,
                anchor="w",
                justify="left",
                wraplength=560,
            )
            body_label.pack(fill="x", pady=(6, 0), anchor="w")

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
