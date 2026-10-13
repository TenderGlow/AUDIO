"""防"事件回调抛异常 -> qFatal 闪退"的回归测试。

背景：2026-10-13 程序一被鼠标碰到就闪退，事件日志是
    故障模块 Qt5Core.dll / 异常 0xc0000409
根因：mouseMoveEvent 读 self._resize_mode，而 __init__ 没初始化它，
      AttributeError -> PyQt 在 Qt 虚函数里 abort。

这里做两件事：
1. 检查事件回调用到的属性是否都在 __init__ 里初始化过；
2. 真的把每种鼠标/窗口事件打一遍，任何异常都必须暴露。
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QPoint, QRect, Qt, QEvent
from PyQt5.QtGui import QMouseEvent, QWheelEvent, QResizeEvent, QCloseEvent

app = QApplication(sys.argv)

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


print("\n=== 1. 事件回调依赖的属性必须已初始化 ===")
settings = Settings()
settings.use_local_proxy = False
win = FloatingWindow(settings)
win.resize(300, 240)
win.show()
app.processEvents()
time.sleep(0.3)
app.processEvents()

# 这些属性会被 mouseMoveEvent / mousePressEvent / mouseReleaseEvent / _do_resize 读取
REQUIRED = ("_dragging", "_drag_offset", "_drag_pending",
            "_resize_mode", "_resize_flags", "_resize_geo", "_resize_start",
            "_arm_targets", "_move_mode", "_move_offset", "_busy", "_pages")
missing = [a for a in REQUIRED if not hasattr(win, a)]
check("回调依赖的属性全部存在", not missing, f"-> 缺 {missing}" if missing else "")

print("\n=== 2. 把每种鼠标事件都打一遍 ===")


def mouse(kind, pos, button=Qt.NoButton, buttons=Qt.NoButton):
    return QMouseEvent(kind, pos, pos, button, buttons, Qt.NoModifier)


points = [QPoint(1, 1), QPoint(150, 120), QPoint(298, 238), QPoint(0, 0)]
errors = []

for p in points:
    for kind, name in ((QEvent.MouseMove, "MouseMove"),
                       (QEvent.MouseButtonPress, "Press"),
                       (QEvent.MouseButtonRelease, "Release"),
                       (QEvent.MouseButtonDblClick, "DblClick")):
        btn = Qt.LeftButton if name in ("Press", "Release", "DblClick") else Qt.NoButton
        try:
            win.mouseMoveEvent(mouse(QEvent.MouseMove, p))
            win.mousePressEvent(mouse(kind, p, btn, Qt.LeftButton))
            win.mouseMoveEvent(mouse(QEvent.MouseMove, p, btn, Qt.LeftButton))
            win.mouseReleaseEvent(mouse(QEvent.MouseButtonRelease, p, Qt.LeftButton))
        except Exception as exc:
            errors.append(f"{name}@{p.x()},{p.y()}: {type(exc).__name__} {exc}")

check("四种鼠标事件在四个角落都不抛异常", not errors,
      "-> " + "; ".join(errors[:3]) if errors else "")

print("\n=== 3. 拖边缩放全程（含 _do_resize）===")
errors2 = []
try:
    win.mousePressEvent(mouse(QEvent.MouseButtonPress, QPoint(2, 120),
                              Qt.LeftButton, Qt.LeftButton))
    win.mouseMoveEvent(mouse(QEvent.MouseMove, QPoint(60, 120),
                             Qt.NoButton, Qt.LeftButton))
    win.mouseReleaseEvent(mouse(QEvent.MouseButtonRelease, QPoint(60, 120),
                                 Qt.LeftButton))
except Exception as exc:
    errors2.append(f"{type(exc).__name__} {exc}")
check("缩放拖动全程无异常", not errors2, "-> " + errors2[0] if errors2 else "")

print("\n=== 4. 标题条事件转发 ===")
errors3 = []
for kind in (QEvent.MouseButtonPress, QEvent.MouseMove, QEvent.MouseButtonRelease):
    try:
        win._on_proxy(mouse(kind, QPoint(20, 10),
                            Qt.LeftButton if kind != QEvent.MouseMove else Qt.NoButton,
                            Qt.LeftButton))
    except Exception as exc:
        errors3.append(f"proxy:{type(exc).__name__} {exc}")
check("标题条事件转发无异常", not errors3, "-> " + errors3[0] if errors3 else "")

print("\n=== 5. 滚轮 / 缩放窗口 / 离开 ===")
errors4 = []
try:
    ev = QWheelEvent(QPoint(100, 60), QPoint(100, 60), QPoint(0, 0),
                     QPoint(0, 120), Qt.NoButton, Qt.NoModifier,
                     Qt.NoScrollPhase, False)
    win.body.set_text("line\n" * 200)
    win.body.wheelEvent(ev)
except Exception as exc:
    errors4.append(f"wheel:{type(exc).__name__} {exc}")
try:
    win.body.wheelEvent(QWheelEvent(QPoint(100, 60), QPoint(100, 60),
                                    QPoint(0, 0), QPoint(0, -120),
                                    Qt.NoButton, Qt.NoModifier,
                                    Qt.NoScrollPhase, False))
except Exception as exc:
    errors4.append(f"wheel-:{type(exc).__name__} {exc}")
try:
    win.setGeometry(QRect(100, 100, 400, 320))
    app.processEvents()
except Exception as exc:
    errors4.append(f"resize:{type(exc).__name__} {exc}")
try:
    win.leaveEvent(QEvent(QEvent.Leave))
except Exception as exc:
    errors4.append(f"leave:{type(exc).__name__} {exc}")
check("滚轮/缩放/离开均无异常", not errors4, "-> " + errors4[0] if errors4 else "")

print("\n=== 6. 显示 / 隐藏 / 移动回调 ===")
errors5 = []
try:
    win.hide(); app.processEvents()
    win.show(); app.processEvents()
    win.start_move_mode(); app.processEvents()
    for _ in range(20):
        app.processEvents(); time.sleep(0.01)
except Exception as exc:
    errors5.append(f"{type(exc).__name__} {exc}")
check("显隐/跟随移动无异常", not errors5, "-> " + errors5[0] if errors5 else "")

print("\n=== 7. 录屏检测自动隐藏不会崩 ===")
errors6 = []
import audio.floating_window as fw
real_risk = fw.capture_risk
try:
    fw.capture_risk = lambda: (True, "模拟录屏")
    win._check_capture_risk()
    fw.capture_risk = lambda: (False, "")
    win._check_capture_risk()
except Exception as exc:
    errors6.append(f"{type(exc).__name__} {exc}")
finally:
    fw.capture_risk = real_risk
check("录屏检测链路无异常", not errors6, "-> " + errors6[0] if errors6 else "")

win.close()
app.processEvents()

print("\n" + "=" * 52)
print(f"通过 {ok} / 失败 {fail}")
print("=" * 52)
sys.exit(1 if fail else 0)