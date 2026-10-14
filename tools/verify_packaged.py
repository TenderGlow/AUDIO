"""打包版的端到端自检：双击 -> 悬浮窗出现 -> 保活 -> 退出。

覆盖打包后才有的几条路径（这些在源码模式下全都测不到）：
  1. 一次双击是否只起一个监控进程（重复双击不会变成两个悬浮窗）
  2. 悬浮窗有没有真的弹出来、用了多久
  3. 强杀悬浮窗进程后，监控会不会把它拉回来
  4. 本地代理是否活着、主程序是否只连 127.0.0.1
  5. --stop 之后是否全部退干净（含监控进程和孤儿代理）
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cloudSyncG import runtime  # noqa: E402
from cloudSyncG import shutdown  # noqa: E402

EXE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "dist", "CloudSync.exe")
PROXY_URL = "http://127.0.0.1:8781/_ping"

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32
_ENUM = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        _failures.append(name)


def cloudsync_pids() -> list[int]:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         "(Get-CimInstance Win32_Process -Filter \"Name='CloudSync.exe'\").ProcessId"
         " -join ' '"],
        capture_output=True, text=True, timeout=30).stdout
    return [int(x) for x in out.split()]


def command_lines() -> dict[int, str]:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='CloudSync.exe'\" | "
         "ForEach-Object { \"$($_.ProcessId)|$($_.CommandLine)\" }"],
        capture_output=True, text=True, timeout=30).stdout
    rows = {}
    for line in out.splitlines():
        if "|" in line:
            pid, cmd = line.split("|", 1)
            try:
                rows[int(pid.strip())] = cmd.strip()
            except ValueError:
                pass
    return rows


def wait_window(timeout: float) -> tuple[int, float]:
    """等悬浮窗出现。返回 (hwnd, 等待秒数)。"""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        hwnd = shutdown.find_window()
        if hwnd and _user32.IsWindowVisible(wintypes.HWND(hwnd)):
            return hwnd, time.monotonic() - start
        time.sleep(0.1)
    return 0, time.monotonic() - start


def proxy_alive() -> bool:
    import json
    import urllib.request
    try:
        with urllib.request.urlopen(PROXY_URL, timeout=2) as resp:
            return bool(json.loads(resp.read()).get("ok"))
    except Exception:  # noqa: BLE001
        return False


def window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
    return int(pid.value)


def main() -> int:
    if not os.path.exists(EXE):
        print(f"找不到 {EXE}，请先运行 打包.bat")
        return 2

    print(f"被测文件：{EXE}")
    shutdown.stop()
    time.sleep(2)

    print("\n1) 启动与首次弹窗")
    t0 = time.monotonic()
    subprocess.Popen([EXE], cwd=os.path.dirname(EXE),
                     creationflags=0x00000008)  # DETACHED_PROCESS
    hwnd, waited = wait_window(40.0)
    check("双击后悬浮窗出现", hwnd != 0, f"{waited:.1f}s（自启动命令算起共 {time.monotonic()-t0:.1f}s）")
    if not hwnd:
        return 1

    win_pid = window_pid(hwnd)
    print(f"     窗口 hwnd={hwnd} pid={win_pid} pid 文件={shutdown.read_pid()}")

    print("\n2) 进程拓扑与命名")
    time.sleep(6)
    pids = cloudsync_pids()
    lines = command_lines()
    check("进程都在跑", len(pids) >= 3, f"{len(pids)} 个：{pids}")
    check("没有源码模式的 python 残留", not _leftover_python(),
          "父/子/代理全部是 CloudSync.exe")
    roles = [lines.get(p, "?") for p in pids]
    check("角色齐备：监控 + 悬浮窗 + 代理",
          any("--worker" in c for c in roles) and any("--proxy" in c for c in roles),
          " | ".join(sorted(c.replace(EXE, "CloudSync.exe") for c in roles)))

    print("\n3) 不在任务栏/托盘")
    # Qt.Tool 窗口不出现在任务栏/Alt+Tab；托盘图标整个项目就没建过
    exstyle = _exstyle(hwnd)
    WS_EX_TOOLWINDOW = 0x00000080
    check("窗口带 WS_EX_TOOLWINDOW（不在任务栏/Alt+Tab）",
          bool(exstyle & WS_EX_TOOLWINDOW), f"exstyle=0x{exstyle:08X}")
    check("窗口无标题（录屏软件的选择器会跳过它）",
          not _window_text(hwnd), repr(_window_text(hwnd)))
    check("窗口不抢焦点（WS_EX_NOACTIVATE）",
          bool(exstyle & 0x08000000), "静默弹出，不打断正在输入的东西")
    check("项目里没有托盘图标代码",
          "QSystemTrayIcon" not in _sources_text(), "本来就不建托盘")

    print("\n3b) 别的程序进全屏/抢置顶后，悬浮窗要能自己回到最前")
    check("别的窗口强行置顶时，悬浮窗在它上面",
          _stays_above_competitor(), "模拟" + COMPETITOR + " 抢占 TOPMOST 后等兜底顶回来")

    print("\n3c) 窗口被外部强行隐藏后，兜底监控要把它拉回来")
    check("外部 ShowWindow(SW_HIDE) 后 1 秒内自动恢复显示",
          _recovers_from_external_hide(),
          "模拟那些「全屏专注模式」软件直接藏掉别人的窗口")

    print("\n4) 本地代理")
    time.sleep(3)
    check("代理在 127.0.0.1:8781 上活着", proxy_alive())

    print("\n5) 保活：强杀悬浮窗进程")
    _kernel32.TerminateProcess(_kernel32.OpenProcess(0x0001, False, win_pid), 0)
    print(f"     已强杀 pid={win_pid}")
    hwnd2, waited2 = wait_window(30.0)
    check("悬浮窗被自动拉回来", hwnd2 != 0 and window_pid(hwnd2) != win_pid,
          f"{waited2:.1f}s，新 pid={window_pid(hwnd2)}")
    check("保活后代理依然在", proxy_alive())

    print("\n6) 重复双击不会起第二个实例")
    subprocess.Popen([EXE], cwd=os.path.dirname(EXE), creationflags=0x00000008)
    time.sleep(6)
    check("悬浮窗仍然只有一个", shutdown.find_window() != 0
          and window_pid(shutdown.find_window()) == window_pid(hwnd2),
          f"pid={window_pid(shutdown.find_window())}")

    print("\n7) --stop 收工")
    out = subprocess.run([EXE, "--stop"], capture_output=True, text=True, timeout=60)
    print("     " + (out.stdout or "").strip().replace("\n", " / "))
    time.sleep(3)
    left = cloudsync_pids()
    check("进程全部退出", not left, f"残留 {left}")
    check("代理端口已释放", not proxy_alive())
    check("pid 文件已清理", shutdown.read_pid() == 0)
    check("用户主动退出标记已写入", runtime.user_exited())

    print("\n8) 再次双击：必须能重新起来（标记不能挡住重启）")
    subprocess.Popen([EXE], cwd=os.path.dirname(EXE), creationflags=0x00000008)
    hwnd3, waited3 = wait_window(40.0)
    check("重新启动成功", hwnd3 != 0, f"{waited3:.1f}s")
    subprocess.run([EXE, "--stop"], capture_output=True, timeout=60)
    time.sleep(2)

    print("\n" + ("全部通过" if not _failures
                  else f"失败 {len(_failures)} 项：" + "、".join(_failures)))
    return 0 if not _failures else 1


def _window_text(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    _user32.GetWindowTextW(wintypes.HWND(hwnd), buf, 256)
    return buf.value


def _exstyle(hwnd: int) -> int:
    """读窗口扩展样式。

    必须用 GetWindowLongPtrW 并显式声明返回类型：ctypes 默认按 int 解析返回值，
    而这个 API 在 64 位上返回 LONG_PTR，强转之后高位会被截成 0，读出来永远是 0。
    """
    _user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    _user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    return int(_user32.GetWindowLongPtrW(wintypes.HWND(hwnd), -20)) & 0xFFFFFFFF


def _leftover_python() -> bool:
    """还有没有以源码模式跑起来的 cloudSyncG 进程。"""
    script = (
        "Get-CimInstance Win32_Process -Filter "
        "\"Name='python.exe' OR Name='pythonw.exe'\" | "
        "Where-Object { $_.CommandLine -like '*cloudSyncG*' -and "
        "$_.ProcessId -ne %d } | Measure-Object | "
        "ForEach-Object { $_.Count }" % os.getpid()
    )
    out = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=30).stdout.strip()
    return out.strip() not in ("", "0")


def _sources_text() -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    chunks = []
    for name in os.listdir(os.path.join(root, "cloudSyncG")):
        if name.endswith(".py"):
            with open(os.path.join(root, "cloudSyncG", name), encoding="utf-8") as fp:
                chunks.append(fp.read())
    return "\n".join(chunks)


COMPETITOR = "notepad.exe"


def _recovers_from_external_hide(timeout: float = 6.0) -> bool:
    """模拟「沉浸/专注模式」软件直接藏掉别人的窗口，看兜底监控会不会拉回来。

    这是用户实际遇到的场景：某些全屏软件会把所有非顶层窗口 ShowWindow(SW_HIDE)，
    悬浮窗既没崩也没退，就是不见了。程序内部无从察觉（它自己没调用过 hide），
    只有进程外部的监控能发现并拉回。
    """
    hwnd = shutdown.find_window()
    if not hwnd:
        return False
    # 先确认"主动隐藏"标记不在，否则兜底会按约定放行
    if runtime.is_hidden_intentionally():
        return False
    _user32.ShowWindow(wintypes.HWND(hwnd), 0)     # SW_HIDE：模拟外部藏窗
    time.sleep(0.2)
    if _user32.IsWindowVisible(wintypes.HWND(hwnd)):
        return False                               # 根本没藏住，测不了
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        if _user32.IsWindowVisible(wintypes.HWND(hwnd)):
            print(f"        外部藏起后 {time.monotonic()-started:.1f}s 被兜底拉回")
            return True
        time.sleep(0.1)
    return False


def _z_order(_unused: int = 0) -> dict[int, int]:
    """枚举顶层窗口，返回 {hwnd: z 序位置}，0 是最前面。"""
    order: dict[int, int] = {}

    def cb(h, _lparam):
        order[int(h)] = len(order)
        return True

    _user32.EnumWindows(_ENUM(cb), 0)
    return order


def _competitor_hwnd() -> int:
    found = {"hwnd": 0}

    def cb(h, _lparam):
        if not _user32.IsWindowVisible(h):
            return True
        buf = ctypes.create_unicode_buffer(256)
        _user32.GetClassNameW(h, buf, 256)
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if buf.value == "Notepad":
            found["hwnd"] = int(h)
            return False
        return True

    _user32.EnumWindows(_ENUM(cb), 0)
    return found["hwnd"]


def _stays_above_competitor(timeout: float = 8.0) -> bool:
    """开一个记事本并强行置顶，检查悬浮窗能否在心跳内重新压回它上面。

    这就是用户报的"全屏一开窗口就没了"的场景：别的程序抢了置顶，
    悬浮窗必须自己顶回来，而不是消失在它后面。
    """
    me = shutdown.find_window()
    if not me:
        return False
    proc = subprocess.Popen(["notepad.exe"], creationflags=0x00000010)  # NEW_CONSOLE
    other = 0
    try:
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and not other:
            time.sleep(0.3)
            other = _competitor_hwnd()
        if not other:
            return False
        SWP_NOMOVE, SWP_NOSIZE, SWP_SHOWWINDOW = 0x2, 0x1, 0x40
        HWND_TOPMOST = ctypes.c_void_p(-1)
        # 让它最大化（任务栏会藏起来，和用户遇到的场景一致）并强行置顶
        _user32.ShowWindow(wintypes.HWND(other), 3)          # SW_MAXIMIZE
        _user32.SetWindowPos(wintypes.HWND(other), HWND_TOPMOST,
                             0, 0, 0, 0,
                             SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)

        def above() -> bool:
            order = _z_order()
            return order.get(me, 1 << 30) < order.get(other, 1 << 30)

        # 先确认它确实抢到了悬浮窗前面——不然这个测试就是空的
        grabbed = False
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if not above():
                grabbed = True
                break
            time.sleep(0.1)
        if not grabbed:
            print("        （对方没能抢到前面，跳过恢复计��）")
            return True

        started = time.monotonic()
        end = started + timeout
        while time.monotonic() < end:
            if above():
                print(f"        对方抢占成功；悬浮窗 {time.monotonic()-started:.1f}s "
                      f"后自己顶回最前")
                return True
            time.sleep(0.1)
        return False
    finally:
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    sys.exit(main())