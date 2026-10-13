"""性能剖析：找出真正的热点，而不是凭印象列清单。

测四块：
  1. 流式渲染：开启描边后每帧真实耗时（addText + strokePath 是大头）
  2. 换行增量成本：长文本追加的边际成本
  3. 空闲 CPU：防录屏轮询每 700ms 枚举一次全部进程，代价多大
  4. 内存与句柄
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt, QElapsedTimer

app = QApplication(sys.argv)

from audio.config import Settings
from audio.floating_window import FloatingWindow
from audio.widgets import StrokedText
from audio import detect

ok = fail = 0
results = {}


def measure(label, fn, rounds=1):
    t = time.perf_counter()
    for _ in range(rounds):
        fn()
    dt = (time.perf_counter() - t) / rounds * 1000
    results[label] = dt
    print(f"  {label:<44} {dt:9.3f} ms")
    return dt


print("\n=== 1. 单帧渲染耗时（开启描边）===")
s = Settings()
s.use_local_proxy = False
win = FloatingWindow(s)
win.resize(268, 219)
win.show()
app.processEvents()

win.body.set_text("【答案】2\n" * 200)
app.processEvents()


def paint_once():
    win.body.repaint()


measure("正文重绘（含描边）", paint_once, 40)

# 对比：关闭描边
win.body.set_stroke((255, 255, 255), 0.0, 0)
measure("正文重绘（关描边）", paint_once, 40)
win.body.set_stroke((255, 255, 255), s.stroke_width, s.stroke_alpha)

print("\n=== 2. 换行增量成本（流式追加）===")
t = StrokedText()
t.resize(252, 180)
t.set_text("")
app.processEvents()


def append_1000():
    for i in range(1000):
        t.append_text("答")


measure("追加 1000 次（冷启动，空表）", append_1000)

t2 = StrokedText()
t2.resize(252, 180)
t2.set_text("答" * 50000)
app.processEvents()
t2._wrap()


def append_1000_long():
    for i in range(1000):
        t2.append_text("答")


measure("已有 5 万字后再追加 1000 次", append_1000_long)

print("\n=== 3. 防录屏轮询的实际代价（每 700ms 一次）===")
measure("list_process_names() 枚举全部进程", detect.list_process_names, 20)
measure("screen_capture_in_progress() 枚举窗口", detect.screen_capture_in_progress, 20)
one = results["list_process_names() 枚举全部进程"]
per_sec = one * (1000 / 700)
print(f"    -> 按 700ms 间隔 = 每秒 {per_sec:.2f} ms，占 1 核 {per_sec / 10:.2f}%")

print("\n=== 4. 空闲 CPU 实测（跑 6 秒，什么都不做）===")
import ctypes
import ctypes.wintypes as wt


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wt.DWORD), ("dwHighDateTime", wt.DWORD)]


def cpu_time():
    k32 = ctypes.windll.kernel32
    c, e, k, u = _FILETIME(), _FILETIME(), _FILETIME(), _FILETIME()
    k32.GetProcessTimes(ctypes.windll.kernel32.GetCurrentProcess(),
                        ctypes.byref(c), ctypes.byref(e),
                        ctypes.byref(k), ctypes.byref(u))
    def t(ft):
        return (ft.dwHighDateTime << 32 | ft.dwLowDateTime) / 1e7
    return t(k) + t(u)


c0 = cpu_time()
w0 = time.perf_counter()
deadline = w0 + 6.0
while time.perf_counter() < deadline:
    app.processEvents()
    time.sleep(0.01)
wall = time.perf_counter() - w0
cpu = cpu_time() - c0
pct = cpu / wall * 100
results["空闲 CPU%"] = pct
print(f"  空闲 6 秒：墙钟 {wall:.1f}s，CPU {cpu:.2f}s -> {pct:.2f}%")
print(f"    折算每天：{pct / 100 * 86400 / 3600:.2f} 小时 CPU 时间")

print("\n=== 5. 内存 ===")
try:
    k32 = ctypes.windll.kernel32
    c, e, k, u = _FILETIME(), _FILETIME(), _FILETIME(), _FILETIME()
    k32.GetProcessTimes(k32.GetCurrentProcess(), ctypes.byref(c),
                        ctypes.byref(e), ctypes.byref(k), ctypes.byref(u))
    cls = ctypes.windll.psapi
    from ctypes import wintypes
    cnt = wintypes.DWORD()
    cls.GetPerformanceInfo(None, 0, ctypes.byref(cnt))
    print("  （略）")
except Exception:
    pass

print("\n" + "=" * 60)
print("汇总：")
for k, v in results.items():
    print(f"  {k:<40} {v:.3f}")
print("=" * 60)

win.close()
app.processEvents()
