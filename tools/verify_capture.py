"""任务一验证：WDA_EXCLUDEFROMCAPTURE 是否真的把窗口排除出捕获。

做法：造一个纯色窗口 → 设置/不设置 display affinity → 各抓一次屏 → 比对像素。
"""
import ctypes
import sys
import time
from ctypes import wintypes

from PyQt5.QtWidgets import QApplication, QWidget
from PyQt5.QtGui import QColor
from PyQt5.QtCore import Qt, QTimer, QRect
from PyQt5.QtCore import QEventLoop

app = QApplication(sys.argv)

WDA_EXCLUDEFROMCAPTURE = 0x11


def grab_bbox(x, y, w, h):
    """直接用 GDI BitBlt 抓一块区域（等价于多数截图/录屏工具的取流方式）。"""
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    hdc = user32.GetDC(0)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    SRCCOPY = 0x00CC0020
    gdi32.BitBlt(mem, 0, 0, w, h, hdc, x, y, SRCCOPY)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD),
                    ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG),
                    ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.biWidth, bi.biHeight = w, -h
    bi.biPlanes, bi.biBitCount, bi.biCompression = 1, 32, 0
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(0, hdc)
    return bytes(buf)


def magenta_ratio(buf, w, h):
    """统计洋红色（255,0,255）像素占比——窗口就是这个颜色。"""
    hit = 0
    for i in range(w * h):
        b, g, r = buf[i * 4], buf[i * 4 + 1], buf[i * 4 + 2]
        if r > 200 and b > 200 and g < 60:
            hit += 1
    return hit / max(1, w * h)


def make_window(color):
    win = QWidget()
    win.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
    win.setAttribute(Qt.WA_TranslucentBackground, True)
    win.setAttribute(Qt.WA_ShowWithoutActivating, True)
    win.setFixedSize(200, 200)
    win.setStyleSheet(f"background: {color};")
    win.move(300, 300)
    win.show()
    app.processEvents()
    return win


def run(label, affinity):
    win = make_window("#FF00FF")
    hwnd = int(win.winId())
    ok_set = ctypes.windll.user32.SetWindowDisplayAffinity(
        wintypes.HWND(hwnd), affinity)
    got = wintypes.DWORD()
    ctypes.windll.user32.GetWindowDisplayAffinity(wintypes.HWND(hwnd),
                                                  ctypes.byref(got))
    time.sleep(0.4)
    app.processEvents()
    buf = grab_bbox(300, 300, 200, 200)
    ratio = magenta_ratio(buf, 200, 200)
    win.close()
    app.processEvents()
    print(f"{label}: SetWindowDisplayAffinity 返回 {bool(ok_set)}, "
          f"读回 0x{got.value:X}, 捕获里洋红占比 {ratio * 100:.1f}%")
    return ratio


print("屏幕尺寸:", app.primaryScreen().size().width(), "x",
      app.primaryScreen().size().height())

base = run("不设置 affinity（对照）", 0x00)
excl = run("设置 WDA_EXCLUDEFROMCAPTURE", WDA_EXCLUDEFROMCAPTURE)

print()
if base > 0.5 and excl < 0.01:
    print(">>> 通过：窗口在 BitBlt 捕获里彻底消失")
elif base > 0.5:
    print(f">>> 部分有效：窗口从 {base*100:.0f}% 降到 {excl*100:.1f}%，"
          "说明仍能看到（可能变成黑块）")
else:
    print(">>> 对照组就没抓到窗口，本机抓屏方式与测试不符，需要换验证手段")