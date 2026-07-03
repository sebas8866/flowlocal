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

BG = ("#FAF9F6", "#1E1C1A")
SIDEBAR_BG = ("#F3F0EA", "#262320")
CARD_BG = ("#FFFFFF", "#2B2825")
CARD_BORDER = ("#E8E4DC", "#3A362F")

TEXT = ("#1A1A1A", "#F2F0EC")
TEXT_SECONDARY = ("#6B6660", "#9A948C")

ACCENT = ("#E8623D", "#E8623D")
ACCENT_HOVER = ("#D4552F", "#F2764F")

NAV_ACTIVE_BG = ("#FFFFFF", "#3A362F")
NAV_HOVER_BG = ("#EAE6DE", "#332F2A")

DANGER = ("#C0392B", "#E74C3C")
DANGER_HOVER = ("#A5321F", "#C0392B")

SUCCESS = ("#3E9B5C", "#4CAF6E")

CORNER_RADIUS = 12
CORNER_RADIUS_SM = 8

PAD_LG = 20
PAD_MD = 16
PAD_SM = 12
PAD_XS = 8


def font(size: int, weight: str = "normal") -> tuple:
    return (FONT_FAMILY, size, weight)
