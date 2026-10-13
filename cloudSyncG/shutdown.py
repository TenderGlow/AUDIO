"""从外部查找并停止正在运行的 cloudSyncG 窗口。

因为窗口既不进任务栏也不进托盘，用户在界面被隐藏时没有任何可见的退出入口，
所以需要一条外部通道。本模块提供三种方式，从优雅到强硬：

- `stop()`  优雅关闭：给窗口发 `WM_CLOSE`，让它自己走 `closeEvent` 保存凭据
- `kill()`  强制结束：直接 `TerminateProcess`
- `status()` 查询状态

**进程身份怎么确认？**
不靠"进程名是 python"来猜——那样会误杀用户其它 python 程序。
启动时把 pid 写进 `%APPDATA%\\cloudSyncG\\cloudSyncG.pid`，停止前核对
"这个 pid 还在、且它确实持有标题为 cloudSyncG 的窗口"，两者都对才动手。
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

WINDOW_TITLE = "cloudSyncG"
PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
WM_CLOSE = 0x0010
TH32CS_SNAPPROCESS = 0x00000002

PID_FILE = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")), "cloudSyncG", "cloudSyncG.pid")

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


# ---------------------------------------------------------------------------
# pid 文件
# ---------------------------------------------------------------------------
def write_pid_file() -> str:
    """启动时调用：记录自己的 pid。"""
    os.makedirs(os.path.dirname(PID_FILE), exist_ok=True)
    with open(PID_FILE, "w", encoding="ascii") as fp:
        fp.write(str(os.getpid()))
    return PID_FILE


def remove_pid_file() -> None:
    """退出时调用：清掉 pid 文件，别留过期记录。"""
    try:
        os.remove(PID_FILE)
    except OSError:
        pass


def read_pid() -> int:
    try:
        with open(PID_FILE, "r", encoding="ascii") as fp:
            return int(fp.read().strip())
    except (OSError, ValueError):
        return 0


def pid_alive(pid: int) -> bool:
    if not pid:
        return False
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    code = wintypes.DWORD()
    _kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
    _kernel32.CloseHandle(handle)
    STILL_ACTIVE = 259
    return code.value == STILL_ACTIVE


# ---------------------------------------------------------------------------
# 窗口查找
# ---------------------------------------------------------------------------
def find_window() -> int:
    """找悬浮窗句柄。窗口即使被 hide() 了句柄依然有效，所以照样找得到。

    **按 PID 匹配，不按标题**：悬浮窗刻意设了空标题（这样 Chrome 等的
    「共享某个窗口」选择器会跳过它），所以已经没有标题可匹配了。
    改用 pid 文件记录的进程号来找，既精确又不会误伤别的窗口。
    """
    pid = read_pid()
    if pid:
        found = {"visible": 0, "any": 0}

        def callback(hwnd, _lparam):
            p = wintypes.DWORD()
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
            if int(p.value) != pid:
                return True
            if not found["any"]:
                found["any"] = int(hwnd)
            # 优先用可见的那个，但**不要求可见**——用户按 Ctrl+Alt+1 把窗口藏起来后，
            # 仍然要能优雅关闭它（走 closeEvent 保存凭据），否则只能强杀。
            if _user32.IsWindowVisible(hwnd):
                found["visible"] = int(hwnd)
                return False
            return True

        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND,
                                             wintypes.LPARAM)
        _user32.EnumWindows(EnumWindowsProc(callback), 0)
        hwnd = found["visible"] or found["any"]
        if hwnd:
            return hwnd

    # 兜底：pid 文件缺失时，退回按标题找（兼容旧版本）
    found = {"hwnd": 0}

    def by_title(hwnd, _lparam):
        buf = ctypes.create_unicode_buffer(256)
        try:
            _user32.GetWindowTextW(hwnd, buf, 256)
        except Exception:  # noqa: BLE001
            return True
        if buf.value == WINDOW_TITLE:
            found["hwnd"] = int(hwnd)
            return False
        return True

    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND,
                                         wintypes.LPARAM)
    _user32.EnumWindows(EnumWindowsProc(by_title), 0)
    return found["hwnd"]


def window_pid(hwnd: int) -> int:
    if not hwnd:
        return 0
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
    return int(pid.value)


def is_running() -> bool:
    pid = read_pid()
    if pid and pid_alive(pid):
        return True
    return bool(find_window())


def _find_by_commandline() -> int:
    """兜底：pid 文件缺失/损坏时，从命令行反查。

    为什么要兜底：pid 文件会被 cleanup() 删掉，一旦删除（或者被清理工具误删），
    光靠 pid 文件就再也定位不到还在跑的实例了——实测踩过这个坑。

    条件要够严，避免误杀别人跑的 python：
      1) 进程名是 python / pythonw
      2) 命令行里含 cloudSyncG-main.py
      3) 该进程持有一个**标题为空**的顶层窗口（这正是悬浮窗的专属特征，
         普通 python 程序不会有一个无标题的顶层窗口）
    """
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Get-CimInstance Win32_Process -Filter "
             "\"Name='python.exe' OR Name='pythonw.exe'\" | "
             "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"],
            capture_output=True, text=True, errors="replace", timeout=15).stdout
    except Exception:  # noqa: BLE001
        return 0
    import json as _json
    try:
        rows = _json.loads(out) if out.strip() else []
    except Exception:  # noqa: BLE001
        return 0
    if isinstance(rows, dict):
        rows = [rows]

    for row in rows:
        cmd = (row.get("CommandLine") or "").lower()
        pid = int(row.get("ProcessId") or 0)
        if not pid or "cloudSyncG-main.py" not in cmd:
            continue
        if _has_titleless_window(pid):
            return pid
    return 0


def _has_titleless_window(pid: int) -> bool:
    """该 pid 是否持有一个无标题的顶层窗口（悬浮窗的特征）。"""
    found = {"ok": False}

    def callback(hwnd, _lparam):
        p = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if int(p.value) != pid:
            return True
        buf = ctypes.create_unicode_buffer(256)
        try:
            _user32.GetWindowTextW(hwnd, buf, 256)
        except Exception:  # noqa: BLE001
            return True
        if buf.value == "":
            found["ok"] = True
            return False
        return True

    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND,
                                         wintypes.LPARAM)
    _user32.EnumWindows(EnumWindowsProc(callback), 0)
    return found["ok"]


def target_pid() -> int:
    """要动的是哪个进程。

    双重确认，避免误杀：pid 文件里记录的进程必须**同时**持有那个无标题窗口。
    两者都对不上时，再走命令行兜底反查。
    """
    hwnd = find_window()
    win_pid = window_pid(hwnd) if hwnd else 0
    file_pid = read_pid()
    if win_pid and file_pid and win_pid == file_pid:
        return win_pid
    if win_pid and pid_alive(win_pid):
        return win_pid
    if file_pid and pid_alive(file_pid):
        return file_pid
    # 兜底：pid 文件没了/对不上，仍然要能找到还活着的实例
    found = _find_by_commandline()
    if found and pid_alive(found):
        return found
    return 0


# ---------------------------------------------------------------------------
# 停止
# ---------------------------------------------------------------------------
def _wait_gone(pid: int, seconds: float) -> bool:
    """等进程真正退出。"""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.1)
    return not pid_alive(pid)


def _terminate(pid: int) -> bool:
    """强制结束指定 pid。"""
    if not pid:
        return False
    handle = _kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if not handle:
        return False
    try:
        return bool(_kernel32.TerminateProcess(handle, 0))
    finally:
        _kernel32.CloseHandle(handle)


def stop() -> bool:
    """优雅关闭，失败则强杀。返回进程是否真的没了。

    **看进程，不能只看窗口**。窗口带 Qt.Tool，关掉窗口不会结束应用；
    只判 IsWindow 消失会误报成功，留下隐形僵尸。

    回退强杀时**必须用开头记下的 pid**：应用关闭流程会先删掉 pid 文件，
    此时进程可能还没退完，重新解析会得到 0，于是杀不掉。
    """
    pid = target_pid()
    if not pid:
        return False
    hwnd = find_window()
    if hwnd:
        _user32.PostMessageW(wintypes.HWND(hwnd), WM_CLOSE, 0, 0)
        if _wait_gone(pid, 8.0):
            cleanup()
            return True
    for _ in range(2):
        _terminate(pid)
        if _wait_gone(pid, 3.0):
            cleanup()
            return True
    cleanup()
    return False


def kill() -> bool:
    """强制结束。"""
    pid = target_pid()
    if not pid:
        return False
    handle = _kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if not handle:
        return False
    try:
        ok = bool(_kernel32.TerminateProcess(handle, 0))
    finally:
        _kernel32.CloseHandle(handle)
    if ok:
        cleanup()
    return ok


def cleanup() -> None:
    remove_pid_file()
    try:
        kill_orphan_proxies()      # 顺手清掉可能残留的代理进程
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# 代理进程清理
# ---------------------------------------------------------------------------
def find_proxy_pids() -> list[int]:
    """找出所有 cloudSyncG 本地代理进程的 pid。

    正常情况下 Job Object 会让代理跟着主程序一起死；但主程序被强杀、
    或 Job 没能生效时就可能留下孤儿。孤儿代理会继续占着连接往上跑，
    所以停止时要顺手清掉。
    """
    pids: list[int] = []
    try:
        snapshot = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snapshot == -1:
            return pids
        try:
            entry = _PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
            if _kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
                while True:
                    if _is_proxy_process(entry.th32ProcessID):
                        pids.append(entry.th32ProcessID)
                    if not _kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                        break
        finally:
            _kernel32.CloseHandle(snapshot)
    except Exception:  # noqa: BLE001
        pass
    return pids


def _is_proxy_process(pid: int) -> bool:
    """是不是我们的代理进程。

    判据：进程名是 python，且命令行含 `cloudSyncG.proxy`。命令行读不到就宁可漏杀，
    绝不误杀用户自己跑的其他 python 程序。
    """
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        size = wintypes.DWORD(512)
        buf = ctypes.create_unicode_buffer(size.value)
        if not _kernel32.QueryFullProcessImageNameW(handle, 0, buf,
                                                   ctypes.byref(size)):
            return False
        name = buf.value.rsplit("\\", 1)[-1].lower()
        if not name.startswith("python"):
            return False
    finally:
        _kernel32.CloseHandle(handle)
    return "cloudSyncG.proxy" in _command_line(pid)


def _command_line(pid: int) -> str:
    """用 WMI 读命令行。读不到就返回空串。"""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\").CommandLine"],
            capture_output=True, text=True, errors="replace", timeout=10)
        return out.stdout or ""
    except Exception:  # noqa: BLE001
        return ""


def kill_orphan_proxies() -> int:
    """清掉所有代理进程。返回清理掉的数量。"""
    killed = 0
    for pid in find_proxy_pids():
        handle = _kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if not handle:
            continue
        try:
            if _kernel32.TerminateProcess(handle, 0):
                killed += 1
        finally:
            _kernel32.CloseHandle(handle)
    return killed


def status() -> dict:
    pid = target_pid()
    return {
        "running": bool(pid) and pid_alive(pid),
        "pid": pid,
        "hwnd": find_window(),
    }


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    if action == "status":
        info = status()
        print(f"状态  : {'运行中' if info['running'] else '未运行'}")
        print(f"pid   : {info['pid'] or '-'}")
        print(f"hwnd  : {info['hwnd'] or '-'}")
        sys.exit(0 if info["running"] else 1)
    if action == "stop":
        print("已优雅停止。" if stop() else "未找到运行中的实例。")
        sys.exit(0)
    if action == "kill":
        print("已强制结束。" if kill() else "未找到运行中的实例。")
        sys.exit(0)
    print(__doc__)
    sys.exit(2)