"""任务二验证：后台静默运行 → 外部完全看不见 → 还能被停掉。

流程：启动真实进程 → 确认它没有任务栏/托盘 → 用 shutdown.stop() 关掉。
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt5.QtWidgets import QApplication
app = QApplication(sys.argv)

from cloudSyncG import shutdown

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable

ok = fail = 0


def check(label, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {label} {extra}")
    else:
        fail += 1
        print(f"  FAIL  {label} {extra}")


print("\n=== 1. 未运行时不应误报 ===")
# 先清干净：套件之间会互相干扰——残留的实例会占住全局热键
#（RegisterHotKey 全局唯一），也会让"杀哪个进程"选错对象。
from harness import cleanup_all
cleanup_all()
check("status 判定为未运行", not shutdown.is_running())

print("\n=== 2. 启动真实后台进程 ===")
proc = subprocess.Popen([PY, "cloudSyncG-main.py"], cwd=ROOT,
                        creationflags=subprocess.CREATE_NO_WINDOW)
# 必须等**窗口**出现，而不只是等 pid 文件：cloudSyncG-main.py 现在还要等本地代理就绪，
# 窗口会比 pid 文件晚一点建好。只等 is_running() 会读到 pid 就断言，读到的 hwnd 是 0。
deadline = time.time() + 40
while time.time() < deadline and not (shutdown.is_running()
                                      and shutdown.find_window()):
    time.sleep(0.4)

check("检测到进程在运行", shutdown.is_running())
info = shutdown.status()
print(f"        pid={info['pid']}  hwnd={info['hwnd']}")
check("pid 文件已写入", info["pid"] > 0)
check("窗口句柄有效", info["hwnd"] > 0)
check("pid 文件与窗口进程一致",
      shutdown.read_pid() == info["pid"],
      f"文件={shutdown.read_pid()} 窗口={info['pid']}")

print("\n=== 3. 外部不可见性（关键）===")
# 任务栏：Qt.Tool 窗口不进任务栏；这里验证没有 WS_EX_APPWINDOW 也没有托盘
hwnd = info["hwnd"]
user32 = __import__("ctypes").windll.user32
ex = user32.GetWindowLongW(__import__("ctypes").c_void_p(hwnd), -20)
print(f"        扩展样式 = 0x{ex & 0xFFFFFFFF:08X}")
check("无任务栏按钮 (WS_EX_APPWINDOW 未置位)", not (ex & 0x00040000))
check("是工具窗口 (WS_EX_TOOLWINDOW 已置位)", bool(ex & 0x00000080))

# 托盘：Qt 托盘图标会创建一个带 "Notification Area" 关联的窗口；这里查 Shell_TrayWnd 下没有 cloudSyncG 图标窗口
found_tray = {"found": False}
EnumWindowsProc = __import__("ctypes").WINFUNCTYPE(
    __import__("ctypes").c_bool, __import__("ctypes").wintypes.HWND,
    __import__("ctypes").wintypes.LPARAM)


def cb(h, _l):
    import ctypes
    if user32.IsWindowVisible(h):
        buf = ctypes.create_unicode_buffer(256)
        try:
            user32.GetClassNameW(h, buf, 256)
        except Exception:
            return True
        if buf.value == "Qt5152QWindowIcon" or "cloudSyncG" == buf.value:
            found_tray["found"] = True
            return False
    return True


user32.EnumWindows(EnumWindowsProc(cb), 0)
check("未创建托盘图标窗口", not found_tray["found"])

print("\n=== 4. 停止它 ===")
t0 = time.time()
stopped = shutdown.stop()
dt = time.time() - t0
check("stop() 成功", stopped, f"耗时 {dt:.1f}s")
time.sleep(0.5)
check("进程已不在运行", not shutdown.is_running())
check("pid 文件已清理", shutdown.read_pid() == 0)

print("\n=== 5. 强杀兜底 ===")
proc2 = subprocess.Popen([PY, "cloudSyncG-main.py"], cwd=ROOT,
                         creationflags=subprocess.CREATE_NO_WINDOW)
deadline = time.time() + 40
while time.time() < deadline and not (shutdown.is_running()
                                      and shutdown.find_window()):
    time.sleep(0.4)
check("第二次启动成功", shutdown.is_running())
check("第二次窗口句柄有效", shutdown.find_window() > 0)
check("kill() 强制结束成功", shutdown.kill())
time.sleep(0.6)
check("强杀后不再运行", not shutdown.is_running())

# 强杀后不能留下孤儿代理
from harness import list_proxies
check("强杀后没有留下孤儿代理进程", len(list_proxies()) == 0,
      f"-> 残留 {list_proxies()}")

cleanup_all(verbose=False)

print("\n" + "=" * 52)
print(f"通过 {ok} / 失败 {fail}")
print("=" * 52)
sys.exit(1 if fail else 0)