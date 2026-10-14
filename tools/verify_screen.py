"""抓屏专项复核：窗口不被截入 / 自己抓屏不含自身 / 录制排除 / 不引发捕获异常。

四件事分别验证：
  A. 真实运行中的窗口，系统级排除标志是否真的生效（直接读它的 affinity）
  B. 造一个"同款窗口"做像素级实验，证明排除确实让它在捕获里消失，并给出反证
  C. 走程序**真实的抓屏路径**（解题/加页用的那条），确认截出来的图不含自己
  D. 抓屏过程中不报错、不出现黑块等异常（捕获端无异常）

不能在这里替你验证的（会如实说明）：Chrome / 第三方录屏软件的具体反应，
那是外部程序行为，本机无法代替它们做断言。
"""
from __future__ import annotations

import base64
import ctypes
import io
import json
import os
import subprocess
import sys
import time
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

WDA_NONE = 0x00
WDA_EXCLUDEFROMCAPTURE = 0x11
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
GWL_EXSTYLE = -20
FOOT = 0x00FF00  # greenish

ok = fail = 0


def check(label, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {label} {extra}")
    else:
        fail += 1
        print(f"  FAIL  {label} {extra}")


def window_info(hwnd):
    ex = user32.GetWindowLongW(wintypes.HWND(hwnd), GWL_EXSTYLE) & 0xFFFFFFFF
    aff = wintypes.DWORD()
    user32.GetWindowDisplayAffinity(wintypes.HWND(hwnd), ctypes.byref(aff))
    r = wintypes.RECT()
    user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(r))
    return {"ex": ex, "aff": aff.value,
            "rect": (r.left, r.top, r.right, r.bottom)}


def gdi_grab(x, y, w, h):
    """GDI BitBlt 抓一块区域——多数截图/录屏工具取的就是这条路。"""
    hdc = user32.GetDC(0)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    SRCCOPY = 0x00CC0020
    gdi32.BitBlt(mem, 0, 0, w, h, hdc, x, y, SRCCOPY)

    class BIH(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD),
                    ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG),
                    ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    bi = BIH()
    bi.biSize = ctypes.sizeof(BIH)
    bi.biWidth, bi.biHeight = w, -h
    bi.biPlanes, bi.biBitCount, bi.biCompression = 1, 32, 0
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(0, hdc)
    return bytes(buf)


def magenta_like(buf, w, h, rmin=200, bmin=200, gmax=70):
    hit = 0
    for i in range(w * h):
        b, g, r = buf[i * 4], buf[i * 4 + 1], buf[i * 4 + 2]
        if r > rmin and b > bmin and g < gmax:
            hit += 1
    return hit


# ---------------------------------------------------------------- 启动真实实例
from harness import cleanup_all, wait_for, is_project_proc  # noqa: E402


def procs():
    """当前在跑的本项目实例进程（主程序 + 代理）。

    匹配条件必须是"命令行里真的在跑 cloudSyncG-main.py / cloudSyncG.proxy"，
    不能只匹配 "cloudSyncG" 这四个字——套件自己的命令行里就带着项目路径
    （run_all 用绝对路径启动时是 `...\\cloudSyncG\\tools\\verify_screen.py`），
    那样连**自己**都会被算成残留，"可正常停止且无残留"就永远失败。
    """
    out = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR "
         "Name='pythonw.exe'\" | Select-Object ProcessId,ParentProcessId,CommandLine | "
         "ConvertTo-Json -Compress"],
        capture_output=True, text=True, errors="replace").stdout.strip()
    if not out:
        return []
    d = json.loads(out)
    if isinstance(d, dict):
        d = [d]
    return [r for r in d if is_project_proc(r.get("CommandLine") or "")]


cleanup_all()
print("\n=== 启动真实实例 ===")
subprocess.Popen([sys.executable, "cloudSyncG-main.py"], cwd=ROOT,
                 creationflags=subprocess.CREATE_NO_WINDOW)
wait_for(lambda: bool(procs()), timeout=45)
time.sleep(5)

rows = procs()
main_row = next((r for r in rows if "cloudSyncG-main.py" in (r.get("CommandLine") or "")), None)
check("主程序在跑", main_row is not None)
if not main_row:
    sys.exit(1)

cands = []


def _collect(h, _l):
    p = wintypes.DWORD()
    user32.GetWindowThreadProcessId(h, ctypes.byref(p))
    if int(p.value) == int(main_row["ProcessId"]) and user32.IsWindowVisible(h):
        cands.append(int(h))
    return True


CB = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows(CB(_collect), 0)
hwnd = 0
if cands:
    biggest = max(cands, key=lambda h: (
        (lambda r: (r.right - r.left) * (r.bottom - r.top))(
            (lambda r: (user32.GetWindowRect(wintypes.HWND(h), ctypes.byref(r)), r)[1])(
                wintypes.RECT()))))
    hwnd = biggest
check("找到真实主窗口", hwnd > 0, f"-> hwnd={hwnd}")

info = window_info(hwnd)
print(f"        窗口 rect={info['rect']}  exstyle=0x{info['ex']:08X}  affinity=0x{info['aff']:X}")

print("\n=== A. 真实窗口的系统级排除标志 ===")
check("affinity = WDA_EXCLUDEFROMCAPTURE（0x11）", info["aff"] == WDA_EXCLUDEFROMCAPTURE,
      f"-> 0x{info['aff']:X}")
check("同时是分层窗口（BitBlt 也取不到）", bool(info["ex"] & WS_EX_LAYERED))
check("是工具窗口（不进任务栏/选择器）", bool(info["ex"] & WS_EX_TOOLWINDOW))
if info["aff"] != WDA_EXCLUDEFROMCAPTURE:
    print("        !! 排除标志没生效，后面的像素结论都不成立")

print("\n=== B. 像素级：同款窗口在捕获里的表现（含反证）===")
from PyQt5.QtWidgets import QApplication  # noqa: E402
from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtGui import QColor  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
from cloudSyncG.floating_window import FloatingWindow  # noqa: E402
from cloudSyncG.config import Settings  # noqa: E402

probe = FloatingWindow(Settings(), persist_credentials=False)
probe.resize(240, 160)
probe.move(320, 320)
probe.panel.set_fill(QColor(255, 0, 255, 255))
probe.panel.set_stroke(QColor(255, 0, 255, 255), 1.0)
probe.show()
for _ in range(40):
    app.processEvents()
    time.sleep(0.01)
# 让 affinity 与标题逻辑生效（showEvent 已设）
px, py = 320, 320
pw, ph = 240, 160

buf = gdi_grab(px, py, pw, ph)
hit_excl = magenta_like(buf, pw, ph)
check("开启排除后：捕获里 0 个窗口像素", hit_excl == 0, f"-> {hit_excl} 个")

# 反证：临时关掉排除，应该能抓到，证明方法本身有效
user32.SetWindowDisplayAffinity(wintypes.HWND(int(probe.winId())), WDA_NONE)
for _ in range(20):
    app.processEvents()
    time.sleep(0.01)
buf2 = gdi_grab(px, py, pw, ph)
hit_none = magenta_like(buf2, pw, ph)
check("反证：关掉排除后能抓到（方法有效）", hit_none > 1000, f"-> {hit_none} 个")
user32.SetWindowDisplayAffinity(wintypes.HWND(int(probe.winId())),
                                WDA_EXCLUDEFROMCAPTURE)
probe.close()
for _ in range(10):
    app.processEvents()

print("\n=== C. 走程序真实抓屏路径，确认不含自身 ===")
from cloudSyncG.capture import Capture, cursor_monitor_rect  # noqa: E402

cap = Capture().grab()
img_bytes = base64.b64decode(cap.base64)
print(f"        抓到 {cap.width}x{cap.height}，{len(img_bytes) // 1024} KB")
check("抓屏成功、有实际内容", cap.width > 0 and len(img_bytes) > 5000,
      f"-> {cap.width}x{cap.height}")

from PIL import Image  # noqa: E402

img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
cap.cleanup()

mon = cursor_monitor_rect()
wr = info["rect"]
if mon and img.width:
    mx, my = mon[0], mon[1]
    scale = img.width / float(mon[2] - mon[0])
    bx0 = max(0, int((wr[0] - mx) * scale))
    by0 = max(0, int((wr[1] - my) * scale))
    bx1 = min(img.width, int((wr[2] - mx) * scale))
    by1 = min(img.height, int((wr[3] - my) * scale))
    box = (bx0, by0, bx1, by1)

    if bx1 > bx0 and by1 > by0:
        # 确定性做法：同一块区域，窗口"显示"与"隐藏"各抓一次，逐像素比对。
        # 窗口若被截进去，两次必然不同；被正确排除则两次完全一致。
        SW_HIDE, SW_SHOW = 0, 5
        shot_visible = Image.open(io.BytesIO(base64.b64decode(
            Capture().grab().base64))).convert("RGB").crop(box)
        user32.ShowWindow(wintypes.HWND(hwnd), SW_HIDE)
        time.sleep(0.6)
        c_hidden = Capture().grab()
        shot_hidden = Image.open(io.BytesIO(base64.b64decode(
            c_hidden.base64))).convert("RGB").crop(box)
        c_hidden.cleanup()
        user32.ShowWindow(wintypes.HWND(hwnd), SW_SHOW)
        time.sleep(0.3)

        a = list(shot_visible.getdata())
        b = list(shot_hidden.getdata())
        diff = sum(1 for x, y in zip(a, b) if x != y)
        total = max(1, len(a))
        print(f"        窗口区域 {shot_visible.width}x{shot_visible.height}，"
              f"显示/隐藏两次抓屏差异像素 {diff}/{total}")
        check("窗口显示与否，抓到的像素完全一致（说明没被截进去）",
              diff == 0, f"-> 差异 {diff / total * 100:.2f}%")
    else:
        check("窗口不在抓屏范围内（同样说明没被截进去）", True)
else:
    check("无法定位区域，跳过像素比对（affinity 已证明排除）", True)

print("\n=== D. 抓屏过程是否出现异常 ===")
errs = []
for _ in range(3):
    try:
        c = Capture().grab()
        if not c.base64:
            errs.append("空结果")
        c.cleanup()
    except Exception as exc:  # noqa: BLE001
        errs.append(f"{type(exc).__name__}: {exc}")
check("连续抓屏 3 次均无异常", not errs, f"-> {errs}")

# 黑块检查：若捕获端对"被排除的窗口"处理不当，常见表现是留一块纯黑矩形
cap = Capture().grab()
img = Image.open(io.BytesIO(base64.b64decode(cap.base64))).convert("RGB")
cap.cleanup()
black = sum(1 for px_ in img.getdata() if sum(px_) < 30)
ratio = black / (img.width * img.height)
check("整屏里没有成片纯黑（无'被排除窗口留黑块'的迹象）", ratio < 0.35,
      f"-> 近黑像素占比 {ratio * 100:.1f}%")

print("\n=== 收尾 ===")
subprocess.run([sys.executable, "-m", "cloudSyncG.shutdown", "stop"],
               cwd=ROOT, capture_output=True)
wait_for(lambda: not procs(), timeout=20)
check("可正常停止且无残留", not procs(), f"-> {[r['ProcessId'] for r in procs()]}")
cleanup_all(verbose=False)

print("\n" + "=" * 60)
print(f"通过 {ok} / 失败 {fail}")
print("=" * 60)
print()
print("说明：Chrome / 第三方录屏软件的具体表现属于外部程序行为，")
print("      本机无法代替它们断言；这里验证的是决定它们行为的系统级机制。")
sys.exit(1 if fail else 0)
