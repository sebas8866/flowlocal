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
