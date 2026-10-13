"""验证：悬浮窗到底会不会被截进去 / 被共享选到。

Q1：Chrome 屏幕共享会不会录进去 —— 检查两件事：
    (a) 系统级捕获能否取到窗口像素（WDA_EXCLUDEFROMCAPTURE 是否生效）
    (b) Chrome 的「共享某个窗口」选择器会不会列出它（那是按窗口标题枚举的）
Q2：自己解题抓屏会不会把自己截进去 —— 关键方法：
    有窗口 / 无窗口 各抓一次同一块区域，逐像素比对。
    完全一致 = 窗口根本不在抓到的图里。
"""
from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QPoint, Qt, QRect
from PyQt5.QtGui import QGuiApplication, QColor

app = QApplication(sys.argv)

from audio.config import Settings
from audio.floating_window import FloatingWindow
from audio.capture import Capture, cursor_monitor_rect

ok = fail = 0


def check(label, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {label} {extra}")
    else:
        fail += 1
        print(f"  FAIL  {label} {extra}")


def pump(n=25):
    for _ in range(n):
        app.processEvents()
        time.sleep(0.01)


print("\n=== Q1-a. 系统级捕获能否取到窗口像素 ===")
user32 = ctypes.windll.user32

# 造一个纯品红对照窗（非分层），证明"如果没排除就一定能被抓到"
class Plain(QWidget := __import__("PyQt5.QtWidgets", fromlist=["QWidget"]).QWidget):
    pass


probe = Plain()
probe.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
probe.setFixedSize(200, 200)
probe.setStyleSheet("background: #FF00FF;")
probe.move(300, 300)
probe.show()
pump()
hwnd_p = int(probe.winId())
WDA_EXCLUDEFROMCAPTURE = 0x11
# 必须先设排除，再抓
user32.SetWindowDisplayAffinity(wintypes.HWND(hwnd_p), WDA_EXCLUDEFROMCAPTURE)
pump(15)
aff = wintypes.DWORD()
user32.GetWindowDisplayAffinity(wintypes.HWND(hwnd_p), ctypes.byref(aff))
check("对照窗已设置 EXCLUDEFROMCAPTURE", aff.value == WDA_EXCLUDEFROMCAPTURE,
      f"-> 0x{aff.value:X}")

from PIL import ImageGrab
grabbed = ImageGrab.grab(bbox=(300, 300, 500, 500))
magenta = sum(c for c, col in grabbed.getcolors(200000)
              if col[0] > 200 and col[2] > 200 and col[1] < 60)
check("对照窗在抓屏里 0 像素（排除生效）", magenta == 0,
      f"-> 品红像素 {magenta}")

# 反证：把排除关掉再抓，应该能抓到 —— 证明方法本身有效
user32.SetWindowDisplayAffinity(wintypes.HWND(hwnd_p), 0x00)
pump(10)
grabbed2 = ImageGrab.grab(bbox=(300, 300, 500, 500))
magenta2 = sum(c for c, col in grabbed2.getcolors(200000)
               if col[0] > 200 and col[2] > 200 and col[1] < 60)
check("反证：关掉排除后能抓到（方法有效）", magenta2 > 1000,
      f"-> 品红像素 {magenta2}")
user32.SetWindowDisplayAffinity(wintypes.HWND(hwnd_p), WDA_EXCLUDEFROMCAPTURE)
pump(10)
probe.close()
pump()

print("\n=== Q2. 自己解题抓屏会不会把自己截进去 ===")
# 方法：给窗口临时上一个**纯品红底**，然后抓屏数品红像素。
# 比"有窗口/无窗口两次抓屏逐像素比对"可靠得多——后者会被桌面本身的
# 变化（时钟、动画）污染，实测在 0~5171 个像素之间随机波动。
s = Settings()
s.use_local_proxy = False
win = FloatingWindow(s)
win.resize(320, 260)
win.show()
pump(40)
win._force_topmost()
pump(20)

geo = win.frameGeometry()
print(f"        悬浮窗位置 {geo.x()},{geo.y()} {geo.width()}x{geo.height()}")

# 换成醒目的品红底：只要它被抓到，数出来的品红像素就必然不为 0
win.panel.set_fill(QColor(255, 0, 255, 255))
win.panel.set_stroke(QColor(255, 0, 255, 255), 1.0)
win.panel.update()
pump(25)


def count_magenta_in(rect):
    import base64
    import io

    from PIL import Image
    cap = Capture().grab(current_monitor_only=False)
    img = Image.open(io.BytesIO(base64.b64decode(cap.base64))).convert("RGB")
    cap.cleanup()
    crop = img.crop((rect.x(), rect.y(), rect.x() + rect.width(),
                     rect.y() + rect.height()))
    return sum(c for c, col in crop.getcolors(200000)
               if col[0] > 200 and col[2] > 200 and col[1] < 60)


inside = QRect(geo.x(), geo.y(), geo.width(), geo.height())
got = count_magenta_in(inside)
check("抓到的图里 0 个品红像素（窗口不在其中）", got == 0,
      f"-> {got} 个品红像素")

# 注意：这里**不能**像 Q1 那样做"关掉排除应该能抓到"的反证——
# 我们的窗口是分层窗口（WA_TranslucentBackground -> WS_EX_LAYERED），
# 而 GDI/BitBlt 抓屏**本来就看不到分层窗口**，与 affinity 无关。
# 这是一层独立的额外保护：即使 affinity 失效，抓屏也照样拍不到它。
GWL_EXSTYLE = -20
hwnd_w = wintypes.HWND(int(win.winId()))
ex = user32.GetWindowLongW(hwnd_w, GWL_EXSTYLE)
check("窗口是分层窗口（BitBlt 本就抓不到）", bool(ex & 0x00080000),
      f"-> exstyle=0x{ex & 0xFFFFFFFF:08X} WS_EX_LAYERED={'有' if ex & 0x00080000 else '无'}")

aff = wintypes.DWORD()
user32.GetWindowDisplayAffinity(hwnd_w, ctypes.byref(aff))
check("且同时设置了 EXCLUDEFROMCAPTURE（双保险）",
      aff.value == WDA_EXCLUDEFROMCAPTURE, f"-> 0x{aff.value:X}")

# 还原成真实外观
win.panel.set_fill(QColor(0, 0, 0, 0))
win.panel.set_stroke(QColor(0, 0, 0, 0), 0)
win.panel.update()
pump(10)

print("\n=== Q1-b. Chrome「共享某个窗口」的选择器会不会列出它 ===")
# Chrome/Windows 的窗口选择器靠枚举顶层窗口 + 取标题。
# 我们把原生标题清空了，所以应该**列不出来**——用户就没法点到它。
found = []


def _enum(h, _l):
    if not user32.IsWindowVisible(h):
        return True
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(h, buf, 256)
    if buf.value:
        found.append(buf.value)
    return True


CB = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows(CB(_enum), 0)
check("原生标题为空（Qt 层）", win.windowTitle() == "", f"-> {win.windowTitle()!r}")
buf = ctypes.create_unicode_buffer(256)
user32.GetWindowTextW(wintypes.HWND(int(win.winId())), buf, 256)
check("原生标题为空（Win32 层）", buf.value == "", f"-> {buf.value!r}")
check("不会被「共享某个窗口」列出来", "Audio" not in found,
      f"-> 可见窗口 {len(found)} 个，其中不含 Audio")

print("\n=== 附：抓屏范围 ===")
rect = cursor_monitor_rect()
print(f"        鼠标所在屏矩形: {rect}")
cap = Capture().grab()
print(f"        实际抓到的图: {cap.width}x{cap.height}")
cap.cleanup()

win.close()
pump()

print("\n" + "=" * 56)
print(f"通过 {ok} / 失败 {fail}")
print("=" * 56)
sys.exit(1 if fail else 0)
