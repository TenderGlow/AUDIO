"""五项专项复核：进程名 / 窗口列表隐身 / 不抢焦点 / 录屏排除 / 父进程路径。

全部给实测证据，不给口头结论。
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
WDA_EXCLUDEFROMCAPTURE = 0x11
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
GWL_EXSTYLE = -20

ok = fail = 0


def check(label, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {label} {extra}")
    else:
        fail += 1
        print(f"  FAIL  {label} {extra}")


def procs():
    out = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" | "
         "Select-Object ProcessId,ParentProcessId,Name,ExecutablePath,CommandLine | "
         "ConvertTo-Json -Compress"],
        capture_output=True, text=True, errors="replace").stdout.strip()
    if not out:
        return []
    d = json.loads(out)
    if isinstance(d, dict):
        d = [d]
    return [r for r in d if "cloudSyncG" in (r.get("CommandLine") or "")]


from harness import cleanup_all, wait_for  # noqa: E402

cleanup_all()
print("\n=== 启动真实实例 ===")
subprocess.Popen([sys.executable, "cloudSyncG-main.py"], cwd=ROOT,
                 creationflags=subprocess.CREATE_NO_WINDOW)
wait_for(lambda: bool(procs()), timeout=45)
time.sleep(5)

rows = procs()
main_row = next((r for r in rows if "cloudSyncG-main.py" in (r.get("CommandLine") or "")), None)
proxy_row = next((r for r in rows if "cloudSyncG.proxy" in (r.get("CommandLine") or "")), None)
check("主程序在跑", main_row is not None)
check("代理进程在跑", proxy_row is not None)
if not (main_row and proxy_row):
    sys.exit(1)
print(f"        主程序 pid={main_row['ProcessId']}   代理 pid={proxy_row['ProcessId']}")

# 主窗口 = 该 pid 面积最大的可见顶层窗口
cands = []


def _collect(h, _l):
    p = wintypes.DWORD()
    user32.GetWindowThreadProcessId(h, ctypes.byref(p))
    if int(p.value) == int(main_row["ProcessId"]):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(h, buf, 256)
        r = wintypes.RECT()
        user32.GetWindowRect(h, ctypes.byref(r))
        cands.append({"hwnd": int(h), "title": buf.value,
                      "visible": bool(user32.IsWindowVisible(h)),
                      "size": (r.right - r.left, r.bottom - r.top),
                      "ex": user32.GetWindowLongW(h, GWL_EXSTYLE) & 0xFFFFFFFF})
    return True


CB = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows(CB(_collect), 0)
shown = [c for c in cands if c["visible"]]
hwnd = max(shown, key=lambda c: c["size"][0] * c["size"][1])["hwnd"] if shown else 0
check("找到主窗口", hwnd > 0, f"-> hwnd={hwnd}")
if not hwnd:
    sys.exit(1)
ex = next(c["ex"] for c in cands if c["hwnd"] == hwnd)

print("\n=== 1. 进程名（任务管理器里显示什么）===")
print(f"        进程名     : {main_row['Name']} / {proxy_row['Name']}")
print(f"        可执行文件 : {main_row['ExecutablePath']}")
print(f"        命令行     : {(main_row['CommandLine'] or '').strip()[-46:]}")
check("进程名是解释器真实名字（未伪装成系统进程）",
      main_row["Name"].lower() in ("python.exe", "pythonw.exe"),
      f"-> {main_row['Name']}")
check("命令行不含系统进程名伪装",
      not any(t in (main_row["CommandLine"] or "").lower()
              for t in ("svchost", "winlogon", "winupdate", "services.exe", "csrss")),
      "-> 未伪装")
check("程序自身标识为 cloudSyncG",
      "cloudsyncg" in (main_row["CommandLine"] or "").lower())

print("\n=== 2. 窗口列表隐身 ===")
check("WS_EX_TOOLWINDOW 已置位（无任务栏按钮）", bool(ex & WS_EX_TOOLWINDOW),
      f"-> exstyle=0x{ex:08X}")
check("WS_EX_APPWINDOW 未置位", not (ex & WS_EX_APPWINDOW))
buf = ctypes.create_unicode_buffer(256)
user32.GetWindowTextW(wintypes.HWND(hwnd), buf, 256)
check("原生标题为空（窗口选择器列不出）", buf.value == "", f"-> {buf.value!r}")

titles = []


def _titles(h, _l):
    if user32.IsWindowVisible(h):
        b = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(h, b, 256)
        if b.value:
            titles.append(b.value)
    return True


user32.EnumWindows(CB(_titles), 0)
# 只检查"属于本进程"的窗口有没有标题。
# 不能全局断言"任何窗口标题都不含 cloudSyncG"——别的程序（比如文件名里带
# cloudSyncG 的记事本、控制台窗口）也会命中，那是假失败。
own_titled = []


def _own(h, _l):
    p = wintypes.DWORD()
    user32.GetWindowThreadProcessId(h, ctypes.byref(p))
    if int(p.value) != int(main_row["ProcessId"]):
        return True
    # 只算**可见**窗口：窗口选择器也只列可见的。
    # 进程里必然还有 "Default IME" / "MSCTFIME UI" 这类输入法辅助窗口，
    # 它们是隐藏的，列出去就是假失败。
    if not user32.IsWindowVisible(h):
        return True
    b = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(h, b, 256)
    if b.value:
        own_titled.append(b.value)
    return True


user32.EnumWindows(CB(_own), 0)
check("本进程没有任何带标题的窗口（窗口选择器列不出）",
      not own_titled, f"-> {own_titled}")

print("\n=== 3. 不抢焦点 ===")
# 注意：不能拿"起一个子进程前后比前台窗口"来判——起 python 子进程本身
# 可能带出控制台窗口抢焦点，那是测试方法的问题，不是被测程序的问题。
# 直接测被测属性：窗口置顶显示时，自己**不应成为前台窗口**。
subprocess.run([sys.executable, "-c",
                "import sys;sys.path.insert(0,'.');from cloudSyncG import shutdown as S;"
                "from PyQt5.QtCore import QTimer;"
                "print(S.status()['hwnd'])"],
               cwd=ROOT, capture_output=True)
time.sleep(0.8)
fg = int(user32.GetForegroundWindow())
check("置顶显示时自己不是前台窗口", fg != hwnd, f"-> 前台={fg} 本窗口={hwnd}")
check("WS_EX_NOACTIVATE 已设置（点击也不激活）", bool(ex & WS_EX_NOACTIVATE),
      f"-> exstyle=0x{ex:08X}")

print("\n=== 4. 录屏排除 ===")
aff = wintypes.DWORD()
user32.GetWindowDisplayAffinity(wintypes.HWND(hwnd), ctypes.byref(aff))
check("WDA_EXCLUDEFROMCAPTURE 生效", aff.value == WDA_EXCLUDEFROMCAPTURE,
      f"-> 0x{aff.value:X}")
check("同时是分层窗口（BitBlt 也抓不到，双保险）", bool(ex & WS_EX_LAYERED))

from PyQt5.QtWidgets import QApplication  # noqa: E402
from PIL import Image  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
from cloudSyncG.capture import Capture  # noqa: E402

cap = Capture().grab(current_monitor_only=False)
img = Image.open(io.BytesIO(base64.b64decode(cap.base64))).convert("RGB")
cap.cleanup()
magenta = sum(c for c, col in img.getcolors(img.width * img.height)
              if col[0] > 200 and col[2] > 200 and col[1] < 60)
check("整屏抓取里没有窗口自身的异常色块", magenta == 0, f"-> {magenta} 像素")

print("\n=== 5. 父进程路径 ===")
check("代理的父进程就是主程序",
      int(proxy_row["ParentProcessId"]) == int(main_row["ProcessId"]),
      f"-> 代理父={proxy_row['ParentProcessId']} 主程序={main_row['ProcessId']}")
print(f"        主程序模块路径 : {main_row['ExecutablePath']}")
print(f"        项目目录       : {ROOT}")
check("可执行文件指向真实解释器，未在项目目录内冒充",
      os.path.dirname(main_row["ExecutablePath"]).lower() != ROOT.lower())
check("入口脚本路径正确",
      (main_row["CommandLine"] or "").strip().endswith("cloudSyncG-main.py"))

print("\n=== 收尾 ===")
from cloudSyncG import shutdown as _S

_before = (_S.target_pid(), _S.find_window(),
           open(_S.PID_FILE).read().strip() if os.path.exists(_S.PID_FILE) else "(none)")
print(f"        [DBG] before stop pid={_before[0]} hwnd={_before[1]} pidfile={_before[2]}")
_r = subprocess.run([sys.executable, "-m", "cloudSyncG.shutdown", "stop"],
                    cwd=ROOT, capture_output=True, text=True, errors="replace")
print(f"        [DBG] stop rc={_r.returncode} "
      f"out={((_r.stdout or '').encode('ascii', 'replace').decode()).strip()!r}")
print(f"        [DBG] after  stop pidfile="
      f"{open(_S.PID_FILE).read().strip() if os.path.exists(_S.PID_FILE) else '(removed)'}")
print(f"        [DBG] still running={[r['ProcessId'] for r in procs()]}")
for r in procs():
    print(f"        [DBG]   pid={r['ProcessId']} alive={_S.pid_alive(int(r['ProcessId']))}")
wait_for(lambda: not procs(), timeout=20)
left = procs()
check("可正常停止且无残留", not left, f"-> {[r['ProcessId'] for r in left]}")
cleanup_all(verbose=False)

print("\n" + "=" * 60)
print(f"通过 {ok} / 失败 {fail}")
print("=" * 60)
sys.exit(1 if fail else 0)
