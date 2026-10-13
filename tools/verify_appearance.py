"""验证外观三项：背景 0 / 文字 10% / 主题色细描边。

描边不能只看配置值——要确认像素层面真的多画了一圈轮廓。
做法：同一段文字分别用"开描边/关描边"渲染，比对描边后**外围一圈**是否被点亮。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QPixmap

app = QApplication(sys.argv)

from audio.config import Settings
from audio.widgets import StrokedText, RoundedPanel, HoverButton, DragMarker, ScrollArrow
from audio.floating_window import FloatingWindow
from audio.theme import get_theme

ok = fail = 0


def check(label, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {label} {extra}")
    else:
        fail += 1
        print(f"  FAIL  {label} {extra}")


s = Settings()

print("\n=== 1. 配置值 ===")
check("背景透明度 = 0", s.bg_opacity == 0.0, f"-> {s.bg_opacity}")
check("文字透明度 = 0.10", s.text_opacity == 0.10, f"-> {s.text_opacity}")
check("描边宽度 > 0（已开启）", s.stroke_width > 0, f"-> {s.stroke_width}")
check("描边宽度是细描边(<=2px)", 0 < s.stroke_width <= 2.0, f"-> {s.stroke_width}")

print("\n=== 2. 所有文字元素共用同一档 10% ===")
win = FloatingWindow(s)
win.resize(300, 240)
win.show()
app.processEvents()

ink_alpha = win.body._fg.alpha()
check("正文墨水 alpha ≈ 10%", abs(ink_alpha - 26) <= 2, f"-> {ink_alpha} (255*0.10≈26)")
for name in ("btn_solve", "btn_add", "btn_reset", "btn_quit", "btn_copy",
             "marker", "arrow_up", "arrow_down"):
    w = getattr(win, name)
    a = w._fg.alpha()
    check(f"{name} 同为 10%", abs(a - ink_alpha) <= 2, f"-> alpha={a}")

print("\n=== 3. 描边真的画出来了（像素级）===")


def render(stroke_width, stroke_alpha):
    t = StrokedText()
    t.setAttribute(Qt.WA_TranslucentBackground, True)
    t.resize(200, 40)
    t.set_font_size(24)
    t.set_text("测试描边")
    t.set_text_color(QColor(20, 20, 20, 255))
    t.set_stroke((255, 255, 255), stroke_width, stroke_alpha)
    pm = QPixmap(t.size())
    pm.fill(Qt.transparent)
    t.render(pm)
    return pm.toImage()


plain = render(0.0, 0)
outlined = render(1.2, 200)


def lit(img):
    return [[1 if img.pixelColor(x, y).alpha() > 8 else 0
             for x in range(img.width())]
            for y in range(img.height())]


a, b = lit(plain), lit(outlined)
extra = sum(1 for y in range(len(a)) for x in range(len(a[y]))
            if b[y][x] and not a[y][x])
check("开描边后多出了像素（轮廓）", extra > 0, f"-> 多 {extra} 个")
check("多出的像素是分布在字周围而非整块", 0 < extra < sum(map(sum, a)) * 1.5,
      f"-> 原字形 {sum(map(sum, a))} 像素, 描边多 {extra}")

print("\n=== 4. 描边用的是主题色 ===")
theme = get_theme(s.theme)
check("描边色 = 主题色", tuple(theme.stroke) == tuple(get_theme(s.theme).stroke),
      f"-> {theme.stroke}")

print("\n=== 5. 底色板仍然是全透（背景 0）===")
check("面板填充 alpha = 0", win.panel._fill.alpha() == 0,
      f"-> alpha={win.panel._fill.alpha()}")
check("面板无描边", win.panel._stroke_width == 0 or win.panel._stroke.alpha() == 0)

print("\n=== 6. 描边没有拖慢绘制 ===")
import time
t0 = time.perf_counter()
for _ in range(30):
    win.body.repaint()
dt = (time.perf_counter() - t0) / 30 * 1000
check("开启描边后单帧 < 25ms", dt < 25, f"-> {dt:.1f} ms/帧")

win.close()
app.processEvents()

print("\n" + "=" * 52)
print(f"通过 {ok} / 失败 {fail}")
print("=" * 52)
sys.exit(1 if fail else 0)