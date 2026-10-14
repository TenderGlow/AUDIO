"""演示：一个普通进程能否查出"哪些窗口开了 WDA_EXCLUDEFROMCAPTURE"。

这不是攻击工具，只是一个诊断——用来证明"这个标志是可被检测的"这句话
到底是什么意思。它只做两件公开、无需任何权限的事：
  1. EnumWindows 枚举顶层窗口（任何程序都能做）
  2. GetWindowDisplayAffinity 查询该窗口的捕获排除状态（任何程序都能做）
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000

AFFINITY_NAMES = {0x00: "WDA_NONE（正常，会被截进去）",
                  0x01: "WDA_MONITOR（截出来是黑的）",
                  0x11: "WDA_EXCLUDEFROMCAPTURE（从捕获中排除）"}

found = []


def on_window(hwnd, _lparam):
    aff = wintypes.DWORD()
    if not user32.GetWindowDisplayAffinity(hwnd, ctypes.byref(aff)):
        return True
    if aff.value == 0:
        return True                      # 正常窗口，不关心
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, 256)
    ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF
    found.append({
        "hwnd": int(hwnd), "pid": int(pid.value), "title": buf.value,
        "affinity": aff.value, "layered": bool(ex & WS_EX_LAYERED),
        "visible": bool(user32.IsWindowVisible(hwnd)),
    })
    return True


CB = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows(CB(on_window), 0)

print("扫描全系统顶层窗口，结果如下：")
print(f"  一共发现 {len(found)} 个窗口被设置了非零的捕获排除标志：")
print()
if not found:
    print("  （本次没有发现。请先启动 cloudSyncG 再运行本脚本。）")
for w in found:
    print(f"  ── hwnd={w['hwnd']}  pid={w['pid']}")
    print(f"     标题      : {w['title']!r}")
    print(f"     排除状态  : 0x{w['affinity']:X}  {AFFINITY_NAMES.get(w['affinity'], '?')}")
    print(f"     分层窗口  : {w['layered']}")
    print(f"     是否可见  : {w['visible']}")
    print()

print("可以看到：")
print("  · 没有用任何特权、没有注入、没有驱动")
print("  · 只靠两个公开 API（EnumWindows + GetWindowDisplayAffinity）")
print("  · 就能准确列出：哪个进程、哪个窗口、开了捕获排除")
print()
print("这就是「可被检测」的含义 —— 标志本身是公开可读的。")
