"""按设计文档 4.4 / 4.5 核对小圆圈与滚动箭头。

4.4：小圆圈「14×14 的纯色实心圆点，无描边」
4.5：「已经到顶/底时继续悬停，计时器自动停止，不会卡在边界空转」
4.5：「鼠标滚轮一格滚 120 单位 -> 滚动 45px」
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QPoint, QPointF, Qt, QEvent, QSize
from PyQt5.QtGui import QPixmap, QWheelEvent, QGuiApplication

app = QApplication(sys.argv)

from cloudSyncG.widgets import DragMarker, ScrollArrow, StrokedText
from cloudSyncG.config import Settings
from cloudSyncG.floating_window import FloatingWindow

ok = fail = 0


def check(label, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {label} {extra}")
    else:
        fail += 1
        print(f"  FAIL  {label} {extra}")


def lit_ratio(widget):
    """渲染后统计"画了东西"的像素占比。

    QWidget.render() 默认会把不透明背景一起画上去，那样量到的永远是 100%。
    必须先开 WA_TranslucentBackground，背景才保持透明，量到的才是控件本身。
    """
    widget.setAttribute(Qt.WA_TranslucentBackground, True)
    pm = QPixmap(widget.size())
    pm.fill(Qt.transparent)
    widget.render(pm)
    img = pm.toImage()
    lit = sum(1 for y in range(img.height())
              for x in range(img.width())
              if img.pixelColor(x, y).alpha() > 8)
    return lit / max(1, img.width() * img.height())


print("\n=== 1. 小圆圈尺寸与实心 ===")
m = DragMarker(2000)
m.resize(14, 14)
check("尺寸 14x14", m.size() == QSize(14, 14), f"-> {m.width()}x{m.height()}")

m.progress = 0.0
r0 = lit_ratio(m)
# 用"中心像素是否被点亮"区分实心 / 空心环——比面积可靠
pm = QPixmap(m.size())
pm.fill(Qt.transparent)
m.setAttribute(Qt.WA_TranslucentBackground, True)
m.render(pm)
img = pm.toImage()
cx, cy = m.width() // 2, m.height() // 2
center_lit = img.pixelColor(cx, cy).alpha() > 8
check("中心像素被点亮（= 实心，非空心环）", center_lit,
      f"中心 alpha={img.pixelColor(cx, cy).alpha()}")
# 实心 8x8 圆盘面积 ≈ 50/196 ≈ 25%；空心环只有一圈，会小很多
check("实心面积占比接近 25%", 0.15 < r0 < 0.45, f"-> 占比 {r0 * 100:.1f}%")

# 计满时也要是实心（进入跟随模式）
m.progress = 1.0
r1 = lit_ratio(m)
check("计满后仍是实心", abs(r1 - r0) < 0.1, f"常态 {r0 * 100:.1f}% vs 计满 {r1 * 100:.1f}%")

print("\n=== 2. 滚轮一格 = 45px ===")
t = StrokedText()
t.resize(240, 100)
t.set_text("\n".join(f"row {i}" for i in range(200)))
app.processEvents()
t.scroll_to_extreme("top")
before = t._scroll
ev = QWheelEvent(QPointF(120, 50), QPointF(120, 50), QPoint(0, 0),
                 QPoint(0, 120), Qt.NoButton, Qt.NoModifier,
                 Qt.NoScrollPhase, False)
check("构造的滚轮事件 angleDelta 正确", ev.angleDelta().y() == 120,
      f"-> {ev.angleDelta().y()}")
t.wheelEvent(ev)
after = t._scroll
check("滚轮往下 -> 内容往上（scroll 增大 45px）",
      abs((after - before) - 45) < 0.5,
      f"-> 实际 {after - before:.1f}px (max_scroll={t.max_scroll():.0f})")

ev_up = QWheelEvent(QPointF(120, 50), QPointF(120, 50), QPoint(0, 0),
                    QPoint(0, -120), Qt.NoButton, Qt.NoModifier,
                    Qt.NoScrollPhase, False)
b2 = t._scroll
t.wheelEvent(ev_up)
check("滚轮往上 -> 内容往下（scroll 减少 45px）",
      abs((t._scroll - b2) + 45) < 0.5,
      f"-> 实际 {t._scroll - b2:.1f}px")

print("\n=== 3. 箭头到边界自动停表 ===")
win = FloatingWindow(Settings())
win.resize(268, 219)
win.show()
app.processEvents()
win.body.resize(240, 120)
win.body.set_text("\n".join(f"row {i}" for i in range(300)))
app.processEvents()

# 先让布局稳定下来（箭头显隐会改变正文高度，max_scroll 会在第一轮刷新后才定型），
# 再滚到底、再刷新，否则量到的是"滚动前的高度算出来的值"。
win._refresh_arrows()
app.processEvents()
win._refresh_arrows()

# 滚到底 -> 下箭头应被告知到界
win.body.scroll_to_extreme("bottom")
win._refresh_arrows()
check("到底时下箭头标记为到界", win.arrow_down._at_bound is True,
      f"scroll={win.body._scroll:.1f} max={win.body.max_scroll():.1f}")
check("到底时下箭头计时器未运行", not win.arrow_down._ticker.isActive())

# 模拟一直悬停：应立刻停在边界，不再空转发信号
fired = []
win.arrow_down.scrolled.connect(lambda d: fired.append(d))
win.arrow_down._hovering = True
win.arrow_down._ticker.start()
for _ in range(20):
    win.arrow_down._tick()
check("到界后不再发滚动信号（不空转）", len(fired) == 0, f"-> 发了 {len(fired)} 次")

# 回到顶部再试上箭头
win.body.scroll_to_extreme("top")
win._refresh_arrows()
check("到顶时上箭头标记为到界", win.arrow_up._at_bound is True)

fired.clear()
win.arrow_up.scrolled.connect(lambda d: fired.append(d))
win.arrow_up._hovering = True
win.arrow_up._ticker.start()
for _ in range(20):
    win.arrow_up._tick()
check("到顶后不再发滚动信号（不空转）", len(fired) == 0, f"-> 发了 {len(fired)} 次")

# 未到界时应当正常滚动
win._refresh_arrows()
fired.clear()
win.arrow_down.scrolled.connect(lambda d: fired.append(d))
win.arrow_down._ticker.start()
for _ in range(5):
    win.arrow_down._tick()
check("未到界时正常发滚动信号", len(fired) > 0, f"-> 发了 {len(fired)} 次")

print("\n=== 4. 离开边界后能恢复 ===")
win.body.scroll_to_extreme("bottom")
win.body._scroll -= 60      # 手动往回一点
win._refresh_arrows()
check("离开边界后标记被复位", win.arrow_down._at_bound is False)
win.arrow_down._ticker.start()
fired.clear()
win.arrow_down._ticker.start()
for _ in range(3):
    win.arrow_down._tick()
check("复位后能继续滚", len(fired) > 0)

win.close()
app.processEvents()

print("\n" + "=" * 52)
print(f"通过 {ok} / 失败 {fail}")
print("=" * 52)
sys.exit(1 if fail else 0)