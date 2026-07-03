"""Home page: welcome header, stat cards, recent dictations."""
from __future__ import annotations

import logging
import time
from typing import Callable, Dict

from flowlocal.ui import theme, widgets

logger = logging.getLogger(__name__)

_RECENT_COUNT = 5
_RECENT_PREVIEW_CHARS = 90


class HomePage:
    def __init__(self, parent, cfg, deps: Dict[str, Callable], app_window) -> None:
        import customtkinter as ctk

        self.cfg = cfg
        self.deps = deps
        self.app_window = app_window

        self.frame = ctk.CTkScrollableFrame(parent, fg_color=theme.BG)
        self.frame.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="stats")

        ctk.CTkLabel(
            self.frame, text="Welcome back", font=theme.font(20, "bold"), text_color=theme.TEXT
        ).grid(row=0, column=0, columnspan=4, sticky="w", padx=theme.PAD_LG, pady=(theme.PAD_LG, theme.PAD_MD))

        self._stat_labels = {}
        stat_defs = [
            ("words_today", "Words today"),
            ("total_words", "Total words"),
            ("avg_wpm", "Avg words/min"),
            ("streak_days", "Day streak"),
        ]
        for i, (key, label) in enumerate(stat_defs):
            card, inner = widgets.make_card(self.frame)
            card.grid(row=1, column=i, sticky="nsew", padx=(theme.PAD_LG if i == 0 else 6, 6 if i < 3 else theme.PAD_LG), pady=(0, theme.PAD_MD))
            value_label = ctk.CTkLabel(
                inner, text="0", font=theme.font(24, "bold"), text_color=theme.ACCENT, anchor="w"
            )
            value_label.pack(anchor="w")
            widgets.secondary_label(inner, label).pack(anchor="w", pady=(2, 0))
            self._stat_labels[key] = value_label

        recent_card, recent_inner = widgets.make_card(self.frame)
        recent_card.grid(row=2, column=0, columnspan=4, sticky="nsew", padx=theme.PAD_LG, pady=(0, theme.PAD_LG))
        widgets.card_header(recent_inner, "Recent dictations").pack(anchor="w", pady=(0, theme.PAD_SM))

        self._recent_list = ctk.CTkFrame(recent_inner, fg_color="transparent")
        self._recent_list.pack(fill="both", expand=True)

        self._empty_label = widgets.secondary_label(
            recent_inner, "Nothing dictated yet — your recent dictations will show up here."
        )

    def grid(self, **kwargs):
        self.frame.grid(**kwargs)

    def tkraise(self):
        self.frame.tkraise()

    def on_show(self) -> None:
        self._refresh_stats()
        self._refresh_recent()

    def _refresh_stats(self) -> None:
        try:
            from flowlocal import history

            s = history.stats()
        except Exception:
            logger.exception("Failed to compute history stats")
            s = {"words_today": 0, "total_words": 0, "avg_wpm": 0.0, "streak_days": 0}

        self._stat_labels["words_today"].configure(text=str(s.get("words_today", 0)))
        self._stat_labels["total_words"].configure(text=str(s.get("total_words", 0)))
        self._stat_labels["avg_wpm"].configure(text=f"{s.get('avg_wpm', 0.0):.0f}")
        self._stat_labels["streak_days"].configure(text=str(s.get("streak_days", 0)))

    def _refresh_recent(self) -> None:
        for child in self._recent_list.winfo_children():
            child.destroy()

        try:
            from flowlocal import history

            entries = history.all()[:_RECENT_COUNT]
        except Exception:
            logger.exception("Failed to load recent history")
            entries = []

        if not entries:
            self._empty_label.pack(anchor="w", pady=(theme.PAD_SM, 0))
            return
        self._empty_label.pack_forget()

        now = time.time()
        import customtkinter as ctk

        for entry in entries:
            row = ctk.CTkFrame(self._recent_list, fg_color="transparent")
            row.pack(fill="x", pady=4)
            row.grid_columnconfigure(1, weight=1)

            time_label = ctk.CTkLabel(
                row,
                text=widgets.relative_time(entry.get("ts", now), now),
                font=theme.font(11),
                text_color=theme.TEXT_SECONDARY,
                width=64,
                anchor="w",
            )
            time_label.grid(row=0, column=0, sticky="w")

            text = entry.get("text", "")
            preview = text if len(text) <= _RECENT_PREVIEW_CHARS else text[:_RECENT_PREVIEW_CHARS] + "…"
            text_label = ctk.CTkLabel(
                row, text=preview, font=theme.font(12), text_color=theme.TEXT, anchor="w", justify="left"
            )
            text_label.grid(row=0, column=1, sticky="ew", padx=(theme.PAD_SM, theme.PAD_SM))

            full_text = text
            copy_btn = widgets.icon_button(
                row, "📋", command=lambda t=full_text: widgets.copy_to_clipboard(self.frame, t)
            )
            copy_btn.grid(row=0, column=2, sticky="e")
