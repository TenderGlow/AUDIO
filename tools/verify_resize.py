"""验证拖边缩放：四边 + 四角共 8 种组合是否都真的生效。

之前那版是空壳——只认右下，拖左边/上边完全没反应。这里逐个方向实测几何变化。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QPoint, QRect, QEvent, Qt
from PyQt5.QtGui import QMouseEvent

app = QApplication(sys.argv)


def _ev(kind, local_pos, global_pos):
    """造鼠标事件。局部坐标与屏幕坐标必须分别给——传混了 _edge_at 会判定落空。"""
    return QMouseEvent(kind, local_pos, global_pos,
                       Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)


from audio.config import Settings
from audio.floating_window import FloatingWindow

ok = fail = 0


def check(label, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {label} {extra}")
    else:
        fail += 1
        print(f"  FAIL  {label} {extra}")


win = FloatingWindow(Settings())
win.resize(268, 219)
win.show()
app.processEvents()

print("\n=== 1. 八个方向的命中判定 ===")
cases = [
    ((1, 110), "l"), ((266, 110), "r"),
    ((134, 1), "t"), ((134, 217), "b"),
    ((1, 1), "lt"), ((266, 1), "rt"),
    ((1, 217), "lb"), ((266, 217), "rb"),
]
for (x, y), expect in cases:
    got = win._edge_at(QPoint(x, y))
    check(f"({x},{y}) -> {expect}", got == expect, f"got {got!r}")

check("中心不命中", win._edge_at(QPoint(134, 110)) == "")

print("\n=== 2. 八个方向都有对应光标（且不是默认箭头）===")
for mode in ("l", "r", "t", "b", "lt", "rt", "lb", "rb"):
    c = win._cursor_for(mode)
    check(f"{mode} 光标非默认", c != Qt.ArrowCursor, f"-> {c}")

print("\n=== 3. 每个方向拖动后几何真的变了吗 ===")
base = QRect(200, 200, 268, 219)

HITS = {"l": (2, 110), "r": (265, 110), "t": (134, 2), "b": (134, 216),
        "lt": (2, 2), "rb": (265, 216), "rt": (265, 2), "lb": (2, 216)}

expectations = {
    "r":  (60, 0,    QRect(200, 200, 328, 219)),
    "l":  (-60, 0,   QRect(140, 200, 328, 219)),
    "b":  (0, 60,    QRect(200, 200, 268, 279)),
    "t":  (0, -60,   QRect(200, 140, 268, 279)),
    "rb": (60, 60,   QRect(200, 200, 328, 279)),
    "lt": (-60, -60, QRect(140, 140, 328, 279)),
    "rt": (60, -60,  QRect(200, 140, 328, 279)),
    "lb": (-60, 60,  QRect(140, 200, 328, 279)),
}

for mode, (dx, dy, expect_rect) in expectations.items():
    win.setGeometry(base)
    app.processEvents()

    hit = HITS[mode]
    local = QPoint(hit[0], hit[1])
    screen = QPoint(base.x() + hit[0], base.y() + hit[1])
    win.mousePressEvent(_ev(QEvent.MouseButtonPress, local, screen))
    check(f"{mode} 按下后进入缩放态",
          win._resize_mode and win._resize_flags == mode,
          f"-> flags={win._resize_flags!r} mode={win._resize_mode}")
    win._do_resize(local, QPoint(screen.x() + dx, screen.y() + dy))
    app.processEvents()
    got = win.geometry()
    check(f"{mode} 拖动后几何正确", got == expect_rect,
          f"got {got.x()},{got.y()} {got.width()}x{got.height()} "
          f"want {expect_rect.x()},{expect_rect.y()} "
          f"{expect_rect.width()}x{expect_rect.height()}")

print("\n=== 4. 下限 180x140 不得被突破 ===")
for mode, (dx, dy) in {"rb": (-400, -400), "lt": (400, 400)}.items():
    win.setGeometry(base)
    app.processEvents()
    hit = HITS[mode]
    local = QPoint(hit[0], hit[1])
    screen = QPoint(base.x() + hit[0], base.y() + hit[1])
    win.mousePressEvent(_ev(QEvent.MouseButtonPress, local, screen))
    win._do_resize(local, QPoint(screen.x() + dx, screen.y() + dy))
    app.processEvents()
    g = win.geometry()
    check(f"{mode} 收到最小尺寸约束", g.width() >= 180 and g.height() >= 140,
          f"-> {g.width()}x{g.height()}")

print("\n=== 5. 缩放后正文会重排、箭头会刷新 ===")
win.setGeometry(QRect(200, 200, 268, 219))
app.processEvents()
win.body.resize(236, 180)      # 固定正文宽度，排除布局时序干扰
app.processEvents()
app.processEvents()
win.body.set_text("  ".join(f"这是第{i}个很长的句子用来测试折行" for i in range(60)))
app.processEvents()
win._refresh_arrows()
h_before = win.body.content_height()
# 必须先按下（设置 _resize_flags / _resize_start），否则 _do_resize 什么都不做
local = QPoint(265, 110)
screen = QPoint(200 + 265, 200 + 110)
win.mousePressEvent(_ev(QEvent.MouseButtonPress, local, screen))
win._do_resize(local, QPoint(screen.x() + 120, screen.y()))
app.processEvents()
h_after = win.body.content_height()
check("拉宽后行数变少 -> 内容高度变小", h_after < h_before,
      f"{h_before:.0f}px -> {h_after:.0f}px")
check("长文本时箭头可见", win.arrow_up.isVisible())

win.close()
app.processEvents()

print("\n" + "=" * 52)
print(f"通过 {ok} / 失败 {fail}")
print("=" * 52)
sys.exit(1 if fail else 0)