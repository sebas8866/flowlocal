"""Dictionary page: personal-vocabulary editor for cfg.vocabulary.

Wires to the same `on_vocabulary_change(list[str])` callback contract as
the old settings_ui.py Text-box editor, just with a nicer chip-list UI and
immediate apply-on-change (add/remove) rather than apply-on-save.
"""
from __future__ import annotations

import logging
from typing import Callable, Dict

from flowlocal.ui import theme, widgets

logger = logging.getLogger(__name__)


class DictionaryPage:
    def __init__(self, parent, cfg, deps: Dict[str, Callable], app_window) -> None:
        import customtkinter as ctk

        self.cfg = cfg
        self.deps = deps
        self.app_window = app_window

        self.frame = ctk.CTkFrame(parent, fg_color=theme.BG)
        self.frame.grid_rowconfigure(2, weight=1)
        self.frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self.frame, text="Dictionary", font=theme.font(20, "bold"), text_color=theme.TEXT
        ).grid(row=0, column=0, sticky="w", padx=theme.PAD_LG, pady=(theme.PAD_LG, theme.PAD_SM))

        widgets.secondary_label(
            self.frame,
            "Names, product terms, and jargon you teach FlowLocal bias both speech recognition and cleanup.",
        ).grid(row=1, column=0, sticky="w", padx=theme.PAD_LG, pady=(0, theme.PAD_MD))

        add_card, add_inner = widgets.make_card(self.frame)
        add_card.grid(row=2, column=0, sticky="new", padx=theme.PAD_LG, pady=(0, theme.PAD_MD))
        add_row = ctk.CTkFrame(add_inner, fg_color="transparent")
        add_row.pack(fill="x")
        add_row.grid_columnconfigure(0, weight=1)

        self._entry_var = ctk.StringVar()
        entry = ctk.CTkEntry(
            add_row,
            textvariable=self._entry_var,
            placeholder_text="Add a word or phrase…",
            corner_radius=theme.CORNER_RADIUS_SM,
            border_color=theme.CARD_BORDER,
            fg_color=theme.CARD_BG,
            text_color=theme.TEXT,
            height=34,
        )
        entry.grid(row=0, column=0, sticky="ew")
        entry.bind("<Return>", lambda _e: self._on_add())
        self._entry_widget = entry

        widgets.primary_button(add_row, "Add", command=self._on_add).grid(
            row=0, column=1, padx=(theme.PAD_SM, 0)
        )

        list_card, list_inner = widgets.make_card(self.frame)
        list_card.grid(row=3, column=0, sticky="nsew", padx=theme.PAD_LG, pady=(0, theme.PAD_LG))
        self.frame.grid_rowconfigure(3, weight=1)
        widgets.card_header(list_inner, "Your terms").pack(anchor="w", pady=(0, theme.PAD_SM))

        self._scroll = ctk.CTkScrollableFrame(list_inner, fg_color="transparent")
        self._scroll.pack(fill="both", expand=True)

        self._empty_label = widgets.secondary_label(self._scroll, "No terms added yet.")

    def grid(self, **kwargs):
        self.frame.grid(**kwargs)

    def tkraise(self):
        self.frame.tkraise()

    def on_show(self) -> None:
        self._render_terms()

    def _render_terms(self) -> None:
        for child in self._scroll.winfo_children():
            if child is not self._empty_label:
                child.destroy()

        terms = list(self.cfg.vocabulary or [])
        if not terms:
            self._empty_label.pack(anchor="w", pady=theme.PAD_SM)
            return
        self._empty_label.pack_forget()

        import customtkinter as ctk

        for term in terms:
            row = ctk.CTkFrame(self._scroll, fg_color=theme.SIDEBAR_BG, corner_radius=theme.CORNER_RADIUS_SM)
            row.pack(fill="x", pady=3)
            row.grid_columnconfigure(0, weight=1)

            ctk.CTkLabel(
                row, text=term, font=theme.font(12), text_color=theme.TEXT, anchor="w"
            ).grid(row=0, column=0, sticky="w", padx=theme.PAD_SM, pady=6)

            widgets.icon_button(
                row, "✕", command=lambda t=term: self._on_remove(t)
            ).grid(row=0, column=1, sticky="e", padx=(0, 4))

    def _on_add(self) -> None:
        value = self._entry_var.get().strip()
        if not value:
            return
        terms = list(self.cfg.vocabulary or [])
        if value in terms:
            self._entry_var.set("")
            return
        terms.append(value)
        self._apply_vocabulary(terms)
        self._entry_var.set("")

    def _on_remove(self, term: str) -> None:
        terms = [t for t in (self.cfg.vocabulary or []) if t != term]
        self._apply_vocabulary(terms)

    def _apply_vocabulary(self, terms) -> None:
        self.cfg.vocabulary = terms
        cb = self.deps.get("on_vocabulary_change")
        if cb:
            try:
                cb(terms)
            except Exception:
                logger.exception("on_vocabulary_change callback failed")
        try:
            self.cfg.save()
        except Exception:
            logger.exception("Failed to save config after vocabulary change")
        self._render_terms()
