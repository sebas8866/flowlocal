"""Design tokens for the FlowLocal app window (Wispr Flow-style light/dark
theme), plus small CTk-configuration helpers.

Colors are plain hex strings; CustomTkinter widgets accept either a single
color (used for both appearance modes) or a `(light, dark)` tuple. We
mostly pass explicit tuples here so the same widget code works in both
modes without extra branching at call sites.
"""
from __future__ import annotations

FONT_FAMILY = "Segoe UI"

# --- color tokens: (light, dark) -------------------------------------------

FONT_FAMILY_SERIF = "Georgia"

# The window outer background (beige) — sidebar sits directly on this, no
# card. The main content area floats as a white rounded panel inset from
# the outer edges (see window.py `_build_page_area`).
BG = ("#F7F5F1", "#1E1C1A")
SIDEBAR_BG = ("#F7F5F1", "#1E1C1A")
PANEL_BG = ("#FFFFFF", "#262320")
CARD_BG = ("#FFFFFF", "#2B2825")
CARD_BORDER = ("#E8E4DC", "#3A362F")

TEXT = ("#1A1A1A", "#F2F0EC")
TEXT_SECONDARY = ("#6B6660", "#9A948C")

ACCENT = ("#E8623D", "#E8623D")
ACCENT_HOVER = ("#D4552F", "#F2764F")

NAV_ACTIVE_BG = ("#ECE9E3", "#33302C")
NAV_HOVER_BG = ("#EAE6DE", "#332F2A")

# Promo card ("Unlimited dictation") — light coral tint, occupies the same
# sidebar slot Wispr Flow uses for its "Upgrade to Pro" upsell box.
PROMO_BG = ("#FDEEE8", "#3A2B24")

# Row hover tint for feed rows on Home/History.
ROW_HOVER_BG = ("#F7F5F1", "#2B2825")
ROW_DIVIDER = ("#EFECE6", "#332F2A")

DANGER = ("#C0392B", "#E74C3C")
DANGER_HOVER = ("#A5321F", "#C0392B")

SUCCESS = ("#3E9B5C", "#4CAF6E")

CORNER_RADIUS = 12
CORNER_RADIUS_SM = 8
CORNER_RADIUS_LG = 16

PAD_LG = 20
PAD_MD = 16
PAD_SM = 12
PAD_XS = 8


def font(size: int, weight: str = "normal") -> tuple:
    return (FONT_FAMILY, size, weight)


def serif_font(size: int, weight: str = "normal") -> tuple:
    return (FONT_FAMILY_SERIF, size, weight)


def format_count(n) -> str:
    """Abbreviate a word count with K/M suffixes, e.g. 22800 -> '22.8K',
    1_250_000 -> '1.3M'. Values under 1000 are returned as plain integers.
    Pure function — importable without customtkinter so it's testable
    headless.
    """
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "0"

    negative = n < 0
    n = abs(n)

    if n >= 1_000_000:
        value = n / 1_000_000
        suffix = "M"
    elif n >= 1_000:
        value = n / 1_000
        suffix = "K"
    else:
        return f"{'-' if negative else ''}{int(n)}"

    text = f"{value:.1f}".rstrip("0").rstrip(".")
    return f"{'-' if negative else ''}{text}{suffix}"
