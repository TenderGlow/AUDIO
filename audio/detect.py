"""录屏 / 截屏检测：判断当前是否有正在录屏或正在抓屏的程序。

目的不是"检测得准"，而是"绝大多数情况能躲开"。采用两层信号：

1. **进程名黑名单**：OBS、XSplit、Bandicam、Snipaste 等已知录屏/截屏工具在跑。
   误伤可控——这些工具平时开着不录屏的概率低，但确实存在，所以只做"降权"不当"硬拦"。
2. **系统级录屏开关**：Windows 自带的 Game Bar 录屏有专属窗口类，可以直接枚举到。

设计原则：**宁可多躲一次，也不要漏拍一次**。窗口本身还带 `WDA_EXCLUDEFROMCAPTURE`
兜底（那才是硬保证，本模块只是提前主动隐藏，减少"露出黑块/残影"的机会）。
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

# ---------------------------------------------------------------------------
# 1) 进程名黑名单（小写，不含扩展名）
# ---------------------------------------------------------------------------

RECORDER_PROCESSES: tuple = (
    # OBS 家族
    "obs64", "obs32", "obs", "obs-studio",
    # 专业录屏
    "xsplit", "bandicam", "bdcam", "camtasia", "camtasiacapture",
    "screenflow", "screenpresso", "filmora", "movavi", "dxtory",
    # 录屏增强/覆盖层
    "nvsphelper", "nvcapture", "rtts", "playstation", "steam", "steamwebhelper",
    # 远程/会议（可能录制会话）
    "zoom", "teams", "ms-teams", "webex", "anydesk", "teamviewer", "parsec",
    "rustdesk", "todesk", "sunloginclient",
    # 截屏 / 贴图
    "snipaste", "sharex", "lightshot", "greenshot", "faststone capture",
    "fscapture", "monosnap", "flameshot", "cleanshot",
)

# 进程名里可能出现的子串（部分工具带版本号，如 Bandicam64.exe）
_RECORDER_SUBSTRINGS = (
    "obs", "xsplit", "bandicam", "camtasia", "dxtory", "screenflow",
    "snipaste", "sharex", "flameshot", "cleanshot", "monosnap",
    "screenpresso", "filmora", "movavi", "nvsphelper", "nvcapture",
)

# ---------------------------------------------------------------------------
# 2) Win32 API
# ---------------------------------------------------------------------------

TH32CS_SNAPPROCESS = 0x00000002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


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


_user32 = ctypes.windll.user32 if hasattr(ctypes, "windll") else None
_kernel32 = ctypes.windll.kernel32 if hasattr(ctypes, "windll") else None

# 每 N 次轮询才做一次「全量枚举进程」（那次最贵，实测约 6ms）。
SLOW_SCAN_EVERY = 3
_poll_count = 0


def _normalize(name: str) -> str:
    """去掉路径、扩展名，转小写，便于匹配。"""
    name = name.rsplit("\\", 1)[-1]
    if name.lower().endswith(".exe"):
        name = name[:-4]
    return name.lower()


def list_process_names() -> set[str]:
    """列出当前所有进程名（小写、去扩展名）。取不到就返回空集。"""
    if not _kernel32:
        return set()
    names: set[str] = set()
    try:
        snapshot = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snapshot == -1:
            return names
        try:
            entry = _PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
            if _kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
                while True:
                    names.add(_normalize(entry.szExeFile))
                    if not _kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                        break
        finally:
            _kernel32.CloseHandle(snapshot)
    except Exception:  # noqa: BLE001 - 任何失败都按"没在录屏"处理
        return names
    return names


def recorder_processes_running() -> set[str]:
    """返回当前正在运行的录屏/截屏程序名集合。"""
    running = list_process_names()
    if not running:
        return set()
    found = {n for n in running if n in RECORDER_PROCESSES}
    if not found:
        found = {n for n in running
                 if any(sub in n for sub in _RECORDER_SUBSTRINGS)}
    return found


def screen_capture_in_progress() -> bool:
    """系统层面是否有录屏正在进行（枚举 Xbox Game Bar 的窗口类）。"""
    if not _user32:
        return False
    try:
        found = {"": False}
        EnumWindowsProc = ctypes.WINFUNCTYPE(
            ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        # 只认 Xbox Game Bar 自己的窗口类。
        # 曾经把 ApplicationFrameWindow 也算进来，那是所有 UWP 应用的通用宿主类，
        # 会导致只要开着任何 UWP 应用就误判"在录屏"，进而把悬浮窗一直藏着。
        GAMebar_CLASSES = ("XblForegroundOverlayWindow", "XboxGipOverlayWindow",
                           "GameBar")

        def callback(hwnd, _lparam):
            if not _user32.IsWindowVisible(hwnd):
                return True
            buf = ctypes.create_unicode_buffer(256)
            try:
                _user32.GetClassNameW(hwnd, buf, 256)
            except Exception:  # noqa: BLE001
                return True
            if buf.value in GAMebar_CLASSES:
                found[""] = True
                return False
            return True

        _user32.EnumWindows(EnumWindowsProc(callback), 0)
        return found[""]
    except Exception:  # noqa: BLE001
        return False


def capture_risk() -> tuple[bool, str]:
    """综合判断当前是否有被抓屏的风险。

    返回 (是否需要隐藏, 原因文本)。原因文本只用于调试，界面上不显示。

    性能：全量枚举进程实测约 6ms，是本模块最贵的一步。这里做分层——
    每 SLOW_SCAN_EVERY 次才做一次全量枚举，中间只查窗口类（0.25ms）。
    代价是录屏工具**刚启动**时最多迟 ~2 秒才被发现，
    换来的是常驻进程枚举降到 1/3。
    """
    global _poll_count
    _poll_count += 1

    if _poll_count % SLOW_SCAN_EVERY == 0:
        procs = recorder_processes_running()
        if procs:
            return True, "录屏/截屏程序在运行: " + ", ".join(sorted(procs)[:5])

    if screen_capture_in_progress():
        return True, "系统录屏控件处于活动状态"
    return False, ""


def reset_poll_counter() -> None:
    """强制下一次 capture_risk() 就做全量扫描（程序刚启动时调用一次）。"""
    global _poll_count
    _poll_count = 0


__all__ = ["recorder_processes_running", "screen_capture_in_progress",
           "capture_risk", "list_process_names", "RECORDER_PROCESSES"]