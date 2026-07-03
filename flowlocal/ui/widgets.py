"""Small reusable widget builders shared across pages, kept consistent with
the design tokens in flowlocal/ui/theme.py.
"""
from __future__ import annotations

from flowlocal.ui import theme

def make_card(parent, **grid_kwargs):
    """A rounded, bordered card frame with standard padding baked into its
    inner content frame. Returns (card_frame, inner_content_frame).
    """
    import customtkinter as ctk

    card = ctk.CTkFrame(
        parent,
        corner_radius=theme.CORNER_RADIUS,
        fg_color=theme.CARD_BG,
        border_width=1,
        border_color=theme.CARD_BORDER,
    )
    inner = ctk.CTkFrame(card, fg_color="transparent")
    inner.pack(fill="both", expand=True, padx=theme.PAD_MD, pady=theme.PAD_MD)
    return card, inner

def card_header(parent, text: str):
    import customtkinter as ctk

    return ctk.CTkLabel(
        parent, text=text, font=theme.font(14, "bold"), text_color=theme.TEXT, anchor="w"
    )

def secondary_label(parent, text: str, **kwargs):
    import customtkinter as ctk

    return ctk.CTkLabel(
        parent, text=text, font=theme.font(11), text_color=theme.TEXT_SECONDARY, anchor="w", **kwargs
    )

def primary_button(parent, text: str, command=None, **kwargs):
    import customtkinter as ctk

    return ctk.CTkButton(
        parent,
        text=text,
        command=command,
        corner_radius=theme.CORNER_RADIUS_SM,
        fg_color=theme.ACCENT,
        hover_color=theme.ACCENT_HOVER,
        text_color="#FFFFFF",
        font=theme.font(12, "bold"),
        **kwargs,
    )

def secondary_button(parent, text: str, command=None, **kwargs):
    import customtkinter as ctk

    return ctk.CTkButton(
        parent,
        text=text,
        command=command,
        corner_radius=theme.CORNER_RADIUS_SM,
        fg_color="transparent",
        hover_color=theme.NAV_HOVER_BG,
        text_color=theme.TEXT,
        border_width=1,
        border_color=theme.CARD_BORDER,
        font=theme.font(12),
        **kwargs,
    )

def icon_button(parent, text: str, command=None, width: int = 28, **kwargs):
    import customtkinter as ctk

    return ctk.CTkButton(
        parent,
        text=text,
        command=command,
        width=width,
        height=width,
        corner_radius=theme.CORNER_RADIUS_SM,
        fg_color="transparent",
        hover_color=theme.NAV_HOVER_BG,
        text_color=theme.TEXT_SECONDARY,
        font=theme.font(12),
        **kwargs,
    )

def day_group_label(ts: float, now: float) -> str:
    """Return the calendar-day group label for a feed entry: 'TODAY',
    'YESTERDAY', or an uppercase 'MONTH DAY' (with year appended if not the
    current year), e.g. 'JUNE 30' or 'DEC 25, 2025'.
    """
    import datetime as _dt

    entry_date = _dt.datetime.fromtimestamp(ts).date()
    today = _dt.datetime.fromtimestamp(now).date()
    delta_days = (today - entry_date).days

    if delta_days == 0:
        return "TODAY"
    if delta_days == 1:
        return "YESTERDAY"

    if entry_date.year == today.year:
        return entry_date.strftime("%B %d").upper().replace(" 0", " ")
    return entry_date.strftime("%B %d, %Y").upper().replace(" 0", " ")

def clock_time(ts: float) -> str:
    """Format a timestamp as a lowercase 12-hour clock time, e.g.
    '12:10 pm'. Strips a leading zero from the hour.
    """
    import datetime as _dt

    text = _dt.datetime.fromtimestamp(ts).strftime("%I:%M %p").lower()
    if text.startswith("0"):
        text = text[1:]
    return text

def relative_time(ts: float, now: float) -> str:
    """Human-friendly relative time string, e.g. 'just now', '5m ago',
    '3h ago', '2d ago', or a short date for anything older.
    """
    import datetime as _dt

    delta = now - ts
    if delta < 0:
        delta = 0
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    if delta < 7 * 86400:
        return f"{int(delta // 86400)}d ago"
    return _dt.datetime.fromtimestamp(ts).strftime("%b %d")

def copy_to_clipboard(widget, text: str) -> None:
    """Copy `text` to the OS clipboard via the widget's Tk root."""
    try:
        widget.clipboard_clear()
        widget.clipboard_append(text)
        widget.update_idletasks()
    except Exception:
        pass

_FEED_PAGE_SIZE = 50

class DictationFeed:
    """Scrollable, searchable, day-grouped list of dictation entries.

    Shared by the Home page (feed only, no chrome of its own) and the
    History page (same feed, plus that page's own "Clear history" button
    above it) so the two views can't visually drift apart.

    Renders lazily: only `_FEED_PAGE_SIZE` entries are built at a time,
    with a "Show more" row to reveal the next page, so a history of
    hundreds of entries doesn't stall the UI on open.

    `on_change` (optional) is called after a delete, so the host page can
    refresh anything else that depends on history (e.g. stat cards).
    """

    def __init__(self, parent, on_change=None) -> None:
        import customtkinter as ctk

        self._on_change = on_change
        self._entries: list = []
        self._query = ""
        self._visible_count = _FEED_PAGE_SIZE

        self.frame = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        self.frame.grid_columnconfigure(0, weight=1)

        self._empty_label = secondary_label(
            self.frame, "Nothing yet — hold your trigger and start talking."
        )

    def grid(self, **kwargs):
        self.frame.grid(**kwargs)

    def pack(self, **kwargs):
        self.frame.pack(**kwargs)

    def set_entries(self, entries: list) -> None:
        self._entries = entries
        self._visible_count = _FEED_PAGE_SIZE
        self.render()

    def set_query(self, query: str) -> None:
        self._query = (query or "").strip().lower()
        self._visible_count = _FEED_PAGE_SIZE
        self.render()

    def render(self) -> None:
        import time as _time

        import customtkinter as ctk

        for child in self.frame.winfo_children():
            if child is not self._empty_label:
                child.destroy()

        entries = self._entries
        if self._query:
            entries = [e for e in entries if self._query in e.get("text", "").lower()]

        if not entries:
            self._empty_label.configure(
                text="No matches." if self._query else "Nothing yet — hold your trigger and start talking."
            )
            self._empty_label.pack(anchor="w", pady=theme.PAD_SM)
            return
        self._empty_label.pack_forget()

        now = _time.time()
        visible = entries[: self._visible_count]

        last_group = None
        for entry in visible:
            ts = entry.get("ts", now)
            group = day_group_label(ts, now)
            if group != last_group:
                ctk.CTkLabel(
                    self.frame,
                    text=group,
                    font=theme.font(10, "bold"),
                    text_color=theme.TEXT_SECONDARY,
                    anchor="w",
                ).pack(fill="x", pady=(theme.PAD_MD if last_group is not None else 0, theme.PAD_XS))
                last_group = group

            self._build_row(entry, now)

        if len(entries) > len(visible):
            more_row = ctk.CTkFrame(self.frame, fg_color="transparent")
            more_row.pack(fill="x", pady=(theme.PAD_SM, 0))
            secondary_button(
                more_row, "Show more", command=self._on_show_more
            ).pack(anchor="w")

    def _on_show_more(self) -> None:
        self._visible_count += _FEED_PAGE_SIZE
        self.render()

    def _build_row(self, entry: dict, now: float) -> None:
        import customtkinter as ctk

        ts = entry.get("ts", now)
        text = entry.get("text", "")

        row = ctk.CTkFrame(self.frame, fg_color="transparent")
        row.pack(fill="x")
        row.grid_columnconfigure(1, weight=1)

        time_label = ctk.CTkLabel(
            row,
            text=clock_time(ts),
            font=theme.font(11),
            text_color=theme.TEXT_SECONDARY,
            width=70,
            anchor="nw",
            justify="left",
        )
        time_label.grid(row=0, column=0, sticky="nw", pady=(theme.PAD_SM, theme.PAD_SM))

        text_label = ctk.CTkLabel(
            row,
            text=text,
            font=theme.font(13),
            text_color=theme.TEXT,
            anchor="w",
            justify="left",
            wraplength=440,
        )
        text_label.grid(row=0, column=1, sticky="new", padx=(theme.PAD_SM, theme.PAD_SM), pady=(theme.PAD_SM, theme.PAD_SM))

        actions = ctk.CTkFrame(row, fg_color="transparent")
        actions.grid(row=0, column=2, sticky="ne", pady=(theme.PAD_SM, theme.PAD_SM))

        copy_btn = icon_button(actions, "📋", command=lambda t=text: copy_to_clipboard(self.frame, t))
        copy_btn.pack(side="left")

        menu_btn = icon_button(actions, "⋮", command=lambda e=entry: self._show_row_menu(e))
        menu_btn.pack(side="left", padx=(4, 0))

        divider = ctk.CTkFrame(self.frame, fg_color=theme.ROW_DIVIDER, height=1)
        divider.pack(fill="x", pady=(0, 0))

        # Hover-reveal tint on the row (best-effort; CTkFrame hover binding
        # is a plain Tk <Enter>/<Leave> bind rather than a built-in CTk
        # feature, so this degrades gracefully to "always visible, no tint"
        # if binding fails in some environment).
        widgets_to_bind = [row, time_label, text_label]
        try:
            for w in widgets_to_bind:
                w.bind("<Enter>", lambda _e, r=row: r.configure(fg_color=theme.ROW_HOVER_BG))
                w.bind("<Leave>", lambda _e, r=row: r.configure(fg_color="transparent"))
        except Exception:
            pass

    def _show_row_menu(self, entry: dict) -> None:
        import tkinter as tk

        menu = tk.Menu(self.frame, tearoff=0)
        menu.add_command(label="Copy", command=lambda: copy_to_clipboard(self.frame, entry.get("text", "")))
        menu.add_command(label="Delete", command=lambda: self._delete_entry(entry))
        try:
            x = self.frame.winfo_pointerx()
            y = self.frame.winfo_pointery()
            menu.tk_popup(x, y)
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass

    def _delete_entry(self, entry: dict) -> None:
        import logging

        logger = logging.getLogger(__name__)
        try:
            from flowlocal import history

            history.delete(entry.get("ts"))
        except Exception:
            logger.exception("Failed to delete history entry")

        self._entries = [e for e in self._entries if e.get("ts") != entry.get("ts")]
        self.render()

        if self._on_change:
            try:
                self._on_change()
            except Exception:
                logger.exception("DictationFeed on_change callback failed")
