"""验证四个按钮真的能通过"悬停"触发——而不是只有外观。

之前所有测试都只验证了"按钮存在、热键注册成功"，从没真的走完一次
enter -> mouseMove(布防) -> 计时 -> 触发 的完整链路。
这里模拟真实鼠标悬停，断言动作确实发生了。
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QPoint, QPointF, Qt, QEvent, QTimer
from PyQt5.QtGui import QMouseEvent

app = QApplication(sys.argv)

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


def press_move(widget, pos):
    """发一次 mouseMove（用于触发 cursor_moved -> 布防）。"""
    widget.mouseMoveEvent(QMouseEvent(
        QEvent.MouseMove, pos, pos, Qt.NoButton, Qt.NoButton, Qt.NoModifier))


def hover(widget, hold_ms=2600, observe=None):
    """完整模拟一次悬停：进入 -> 移动布防 -> 等计时走完。

    observe: 计时过程中的回调，用来在 leaveEvent 清零之前采样进度。
    """
    center = QPoint(widget.width() // 2, widget.height() // 2)
    widget.enterEvent(QEvent(QEvent.Enter))
    press_move(widget, center)
    peak = 0.0
    deadline = time.monotonic() + hold_ms / 1000.0
    while time.monotonic() < deadline:
        app.processEvents()
        peak = max(peak, widget.progress)
        if observe:
            observe()
        time.sleep(0.01)
    widget.leaveEvent(QEvent(QEvent.Leave))
    app.processEvents()
    return peak


settings = Settings()
# 缩短时长，让测试跑得快（行为逻辑不变）
settings.solve_delay_ms = 300
settings.add_page_delay_ms = 300
settings.reset_delay_ms = 300
settings.quit_delay_ms = 300
settings.copy_delay_ms = 300
settings.marker_delay_ms = 300
# 关掉本地代理与网络，避免测试真的发请求
settings.use_local_proxy = False

win = FloatingWindow(settings)
win.resize(300, 240)
win.show()
app.processEvents()
time.sleep(0.4)
app.processEvents()

print("\n=== 1. 按钮是否真的接到了鼠标事件 ===")
for name in ("btn_solve", "btn_add", "btn_reset", "btn_copy"):
    b = getattr(win, name)
    check(f"{name} 启用了鼠标追踪", b.hasMouseTracking() is True)
    check(f"{name} 尺寸有效", b.width() > 0 and b.height() > 0,
          f"-> {b.width()}x{b.height()}")
    check(f"{name} 初始为未布防", b._armed is False)
    check(f"{name} 可用", b.isEnabled())

print("\n=== 2. 完整悬停链路：进度条是否真的在走 ===")
# 注意：leaveEvent 会把 progress 清零，所以必须在悬停过程中采样，不能事后读。
peak = hover(win.btn_copy, hold_ms=200)
check("悬停过程中 progress 持续增长", peak > 0.2,
      f"-> 峰值 {peak:.2f}（leaveEvent 后会被清零，属正常）")

print("\n=== 3. 悬停到底是否触发动作 ===")
calls = []
real_copy = win.on_copy
# 用「包装」而不是 disconnect 真正的处理函数——否则动作根本不会发生
win.btn_copy.hover_triggered.disconnect()
win.btn_copy.hover_triggered.connect(lambda: (calls.append(1), real_copy()))
hover(win.btn_copy, hold_ms=800)
check("悬停够时长后触发复制", len(calls) > 0, f"-> 触发 {len(calls)} 次")

print("\n=== 4. 未布防时不该触发（防误触）===")
calls.clear()
win.btn_copy.leaveEvent(QEvent(QEvent.Leave))
win.btn_copy._armed = False          # 模拟"刚显示、还没移动过"
win.btn_copy._hovering = True
win.btn_copy._ticker.start()
deadline = time.monotonic() + 1.2
while time.monotonic() < deadline:
    app.processEvents()
    time.sleep(0.01)
check("未布防时不会触发", len(calls) == 0, f"-> 误触发 {len(calls)} 次")

print("\n=== 5. 重置按钮（不联网，安全）===")
fired = []
real_reset = win.on_reset
win.btn_reset.hover_triggered.disconnect()
win.btn_reset.hover_triggered.connect(lambda: (fired.append(1), real_reset()))
win.body.set_text("旧内容")
hover(win.btn_reset, hold_ms=800)
check("重置按钮能触发", len(fired) > 0, f"-> {len(fired)} 次")
check("重置后正文被清空", win.body.text() == "",
      f"-> 实际 {win.body.text()!r}")

print("\n=== 6. 小圆圈跟随移动 ===")
moved = []
real_move = win.start_move_mode
win.marker.hover_triggered.disconnect()
win.marker.hover_triggered.connect(lambda: (moved.append(1), real_move()))
hover(win.marker, hold_ms=800)
check("小圆圈能触发跟随模式", len(moved) > 0, f"-> {len(moved)} 次")
check("已进入跟随移动状态", win._move_mode is True)

print("\n=== 7. 按钮文案切换（加页后解题变发送）===")
win._set_staging(True)
check("加页后解题变『发送』", win.btn_solve._text == "发送",
      f"-> {win.btn_solve._text!r}")
check("加页后重置变『取消』", win.btn_reset._text == "取消",
      f"-> {win.btn_reset._text!r}")
win._set_staging(False)
check("取消暂存后恢复『解题』", win.btn_solve._text == "解题")

print("\n=== 8. 端到端：悬停「解题」是否真的干活 ===")
# 前面只证明"信号发出来了"。这里要证明它真的走到了发请求那一步。
# 做法：拦掉网络与抓屏，只看状态流转。
from cloudSyncG.capture import Capture

captured = {"n": 0}
real_grab = Capture.grab


def fake_grab(self, *a, **kw):
    captured["n"] += 1
    self.base64 = "ZmFrZQ=="          # 假 base64，够走流程
    self.width, self.height = 800, 600
    self.path = ""
    return self


Capture.grab = fake_grab
requested = {"n": 0}
real_start = win._start_request


def spy_start(pages, info):
    requested["n"] += 1
    requested["pages"] = len(pages)
    requested["info"] = info


win._start_request = spy_start     # 不真的联网

win.on_solve()                      # 直接触发（等价于悬停满时长）
app.processEvents()
# 注意：不能一看到 grab 被调用就退出——那发生在**工作线程**，
# 排队中的信号还没投递到主线程，_start_request 自然还没被调用。必须继续转事件循环。
deadline = time.monotonic() + 15
while time.monotonic() < deadline and requested["n"] == 0:
    app.processEvents()
    time.sleep(0.02)

check("抓屏被真正调用了", captured["n"] > 0, f"-> 调用 {captured['n']} 次")
check("抓到后进入了发请求流程", requested["n"] > 0,
      f"-> _start_request 被调 {requested['n']} 次")
if requested["n"]:
    check("带着 1 张图去请求", requested.get("pages") == 1,
          f"-> {requested.get('pages')} 张")

# 注意：按钮禁用是在**真正的** _start_request 里做的，这里它被 spy 替换掉了，
# 所以这条要单独验证，不能跟着上面的 spy 一起断言。
win._start_request = real_start
requested["n"] = 0
win._busy = False
win.body.set_text("x")
captured["n"] = 0
win.on_solve()
deadline = time.monotonic() + 15
while time.monotonic() < deadline and win._busy is not True:
    app.processEvents()
    time.sleep(0.02)
check("请求进行中按钮被禁用（防重复提交）",
      win.btn_solve.isEnabled() is False,
      f"-> isEnabled={win.btn_solve.isEnabled()} _busy={win._busy}")

# 真实 SolveWorker 已经在跑，等它结束再收尾
deadline = time.monotonic() + 90
while time.monotonic() < deadline and win._busy:
    app.processEvents()
    time.sleep(0.05)
check("请求跑完后按钮恢复可用", win.btn_solve.isEnabled() is True)
# 这里喂的是假 base64（"ZmFrZQ=="），后端必然拒绝，所以正文里是失败提示。
# 真实端到端（真图、真请求、真答案）由 verify_multipage.py 覆盖。
check("状态栏到达终态（完成或出错）",
      win.status.text() in ("出错",) or "完成" in win.status.text(),
      f"-> {win.status.text()!r}")
check("busy 已复位", win._busy is False)

win._start_request = spy_start
win._busy = False

print("\n=== 9. 加页：是否真的缓存而不是直接发请求 ===")
# fake_grab 上面被换回真实实现了，这里要重新装上，否则统计不到
Capture.grab = fake_grab
captured["n"] = 0
requested["n"] = 0
win.on_add_page()
deadline = time.monotonic() + 15
while time.monotonic() < deadline and captured["n"] == 0:
    app.processEvents()
    time.sleep(0.02)
# 同上：抓到屏后还要等信号投递回来，才谈得上进暂存区
deadline2 = time.monotonic() + 8
while time.monotonic() < deadline2 and len(win._pages) == 0:
    app.processEvents()
    time.sleep(0.02)
check("加页也抓了屏", captured["n"] > 0, f"-> {captured['n']} 次")
check("加页不发请求（只缓存）", requested["n"] == 0,
      f"-> 竟然发了 {requested['n']} 次")
check("页面进了暂存区", len(win._pages) == 1, f"-> {len(win._pages)} 页")
check("加页后按钮变『发送/取消』",
      win.btn_solve._text == "发送" and win.btn_reset._text == "取消")

Capture.grab = real_grab
for p in win._pages:
    p.cleanup()
win._pages.clear()
win._set_staging(False)
# 收尾：恢复真实实现
Capture.grab = real_grab
win._start_request = real_start
win._busy = False
for b in win._hover_buttons:
    b.set_active(True)

win.close()
app.processEvents()

print("\n" + "=" * 52)
print(f"通过 {ok} / 失败 {fail}")
print("=" * 52)
sys.exit(1 if fail else 0)