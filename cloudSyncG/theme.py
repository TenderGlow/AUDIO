"""配色：只有底色 / 文字 / 描边三色，不做任何彩色区分。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    name: str
    bg: tuple[int, int, int]      # 底色板
    fg: tuple[int, int, int]      # 前景"墨水"
    stroke: tuple[int, int, int]  # 文字描边色


LIGHT = Theme(name="light", bg=(255, 255, 255), fg=(30, 30, 30), stroke=(255, 255, 255))
DARK = Theme(name="dark", bg=(24, 24, 24), fg=(235, 235, 235), stroke=(24, 24, 24))

_THEMES = {"light": LIGHT, "dark": DARK}

# 悬浮窗最小尺寸（下限 180×140）
MIN_SIZE = (180, 140)


def get_theme(name: str) -> Theme:
    return _THEMES.get((name or "light").lower(), LIGHT)


__all__ = ["Theme", "LIGHT", "DARK", "get_theme", "MIN_SIZE"]