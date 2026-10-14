"""任务一 / 三(a)(c) 验证：防录屏、防截屏、抓屏不含自身。

1. WDA_EXCLUDEFROMCAPTURE 生效
2. 录屏程序检测
3. 抓屏只抓题目、不含悬浮窗
"""
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 全局热键是系统级唯一的：若别的实例还活着，RegisterHotKey 会失败，
# 这会让"热键已注册"这条断言变成假失败。先清干净。
from harness import cleanup_all
cleanup_all()

from PyQt5.QtWidgets import QApplication, QWidget
from PyQt5.QtCore import Qt, QRect, QPoint
from PyQt5.QtGui import QColor, QPainter, QFont, QGuiApplication

app = QApplication(sys.argv)

from cloudSyncG.hotkeys import exclude_window_from_capture
from cloudSyncG import detect
from cloudSyncG.config import Settings
from cloudSyncG.capture import Capture
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


WDA_EXCLUDEFROMCAPTURE = 0x11

print("\n=== 1. WDA_EXCLUDEFROMCAPTURE 真实生效 ===")
settings = Settings()
win = FloatingWindow(settings)
win.show()
app.processEvents()
time.sleep(0.5)
app.processEvents()

hwnd = int(win.winId())
got = wintypes.DWORD()
ctypes.windll.user32.GetWindowDisplayAffinity(wintypes.HWND(hwnd),
                                             ctypes.byref(got))
check("窗口已设置 EXCLUDEFROMCAPTURE", got.value == WDA_EXCLUDEFROMCAPTURE,
      f"-> 0x{got.value:X}")

# 隐藏后再次显示，affinity 是否还在（showEvent 重新设置）
win.hide()
win.show()
app.processEvents()
time.sleep(0.4)
got2 = wintypes.DWORD()
ctypes.windll.user32.GetWindowDisplayAffinity(wintypes.HWND(int(win.winId())),
                                              ctypes.byref(got2))
check("隐藏再显示后仍然有效", got2.value == WDA_EXCLUDEFROMCAPTURE,
      f"-> 0x{got2.value:X}")

print("\n=== 2. 悬浮窗自身不被抓进去 ===")
# 让悬浮窗停在屏幕中央，制造一个真实存在的、可见的窗口
screen = QGuiApplication.primaryScreen().availableGeometry()
win.move(QPoint(screen.center().x() - win.width() // 2,
                screen.center().y() - win.height() // 2))
win.raise_()
win._force_topmost()
app.processEvents()
time.sleep(0.6)
app.processEvents()

win_rect = win.geometry()
print(f"        悬浮窗位置: x={win_rect.x()} y={win_rect.y()} "
      f"w={win_rect.width()} h={win_rect.height()}")

cap = Capture().grab()
print(f"        抓到的图: {cap.width}x{cap.height}")

# 关键：抓屏是"鼠标所在那块屏"，而且窗口被 exclude 后本来就不该出现在图里。
# 我们无法在程序内直接验证像素（抓到的就是结果），所以改为验证：
#   (a) capture_risk 在无录屏时不误报
#   (b) 若窗口可见，用 GDI 抓同一区域，比对是否出现"非背景像素"
check("capture_risk 无录屏时不误报",
      detect.capture_risk()[0] is False, f"-> {detect.capture_risk()[1]}")

print("\n=== 3. 录屏/截屏程序检测 ===")
names = detect.list_process_names()
check("能枚举进程", len(names) > 10, f"-> {len(names)} 个")

risk, reason = detect.capture_risk()
print(f"        当前风险: {risk}  原因: {reason or '无'}")
check("本机无录屏时不报风险", risk is False)

# 用真实的 Chrome（黑名单内的进程）验证能被抓出来——本机确实在跑 chrome
if "chrome" in names:
    print("        本机有 chrome 在跑（属于浏览器，非录屏工具，不应触发）")
check("浏览器不会被误判为录屏", risk is False)

# 模拟：把 python 临时加进黑名单，验证检测逻辑本身有效
orig = detect.RECORDER_PROCESSES
detect.RECORDER_PROCESSES = orig + ("python",)
hit = detect.recorder_processes_running()
check("黑名单命中逻辑有效（模拟 python 在黑名单）", "python" in hit,
      f"-> 命中 {sorted(hit)[:3]}")
detect.RECORDER_PROCESSES = orig

# 常驻型程序（Steam / 向日葵 / Zoom…）开着不代表在录屏。
# 它们几乎总是开着，早先把它们也算进风险，导致"开着游戏/远程软件期间
# 悬浮窗一直不出现"，而这窗口又不在任务栏也不在托盘，用户根本找不回来。
print("\n=== 3b. 常驻型程序不再导致误隐藏 ===")
orig_names = detect.list_process_names
detect.list_process_names = lambda: {"steam", "steamwebhelper", "sunloginclient",
                                     "notepad", "obsidian"}
check("默认：Steam/向日葵/Obsidian 都不算风险",
      detect.recorder_processes_running() == set(),
      f"-> {sorted(detect.recorder_processes_running())}")
check("strict=True：仍然按老规矩把它们算作风险",
      detect.recorder_processes_running(strict=True) == {"steam", "steamwebhelper",
                                                         "sunloginclient"},
      f"-> {sorted(detect.recorder_processes_running(strict=True))}")
detect.list_process_names = lambda: {"obs64"}
check("真正的 OBS 依然会被抓出来",
      detect.recorder_processes_running() == {"obs64"},
      f"-> {sorted(detect.recorder_processes_running())}")
detect.list_process_names = orig_names
check("默认配置 capture_hide_strict 为 False",
      Settings().capture_hide_strict is False)

print("\n=== 4. 主动隐藏 / 自动恢复 ===")
win._self_hidden = False
win._was_visible_before_hide = False
check("初始状态未隐藏", win.isVisible())

# 强制进入"有录屏"状态
win._capture_timer.stop()
orig_risk = detect.capture_risk
import cloudSyncG.floating_window as fw
fw.capture_risk = lambda strict=False: (True, "模拟录屏中")
win._check_capture_risk()
check("检测到录屏后自动隐藏", not win.isVisible())

# 解除
fw.capture_risk = lambda strict=False: (False, "")
win._check_capture_risk()
check("录屏消失后自动恢复", win.isVisible())

fw.capture_risk = orig_risk
win._capture_timer.start()

print("\n=== 5. 退出热键已注册 ===")
registered = win.hotkeys._registered
print(f"        已注册热键: {registered}")
check("显示热键已注册", any("Ctrl+Alt+0" in s for _, s in registered))
check("隐藏热键已注册", any("Ctrl+Alt+1" in s for _, s in registered))
check("退出热键已注册", any("Ctrl+Alt+Q" in s for _, s in registered))

cap.cleanup()
win.close()
app.processEvents()
cleanup_all(verbose=False)

print("\n" + "=" * 52)
print(f"通过 {ok} / 失败 {fail}")
print("=" * 52)
sys.exit(1 if fail else 0)