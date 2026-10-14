"""监控进程（watchdog）：保证悬浮窗一直活着。

**为什么要有它**
用户点一下 exe 之后，真正弹悬浮窗的是它的子进程。只要子进程因为任何原因
消失（崩溃、被误关、更新出错、句柄失效），这个进程就把它重新拉起来。

**唯一不拉起来的例外**：用户主动点了悬浮窗的「退出」按钮，
或者从命令行执行了 `--stop`。这两种情况由 `runtime.user_exit.flag` 这个
落盘标记表达 —— 退出方亲手写，监控方只管读，双方不靠通信。

**这个进程本身有多轻**
它不导入 PyQt、不建窗口、不占一个任务栏/托盘位，就是一个 `sleep` 循环，
外加一个命名互斥量保证"点两下 exe 只起一个监控"。整份代码没有任何 Qt 依赖，
因为它一旦因为 Qt 初始化失败而崩溃，就没人拉起悬浮窗了。

**异常退出时的退避**
如果悬浮窗一启动就崩（比如配置坏了），无脑重启会变成刷屏式重启。
所以统计"活不到 15 秒就算一次快速失败"，连续 5 次就歇 60 秒再试，
既保住长期可用，也不至于把 CPU 跑满。
"""
from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from ctypes import wintypes

from . import runtime
from . import shutdown
from .shutdown import pid_alive

# 拉起子进程用的窗口/控制台标志
DETACHED_PROCESS = 0x00000008
CREATE_NO_WINDOW = 0x08000000

# 低于这个存活时长就算"快速失败"（秒）
MIN_HEALTHY_SECONDS = 15.0
# 连续快速失败多少次后进入长休息（秒）
MAX_FAST_FAILURES = 5
COOLDOWN_SECONDS = 60.0
# 轮询间隔
POLL_SECONDS = 0.5
# 可见性兜底的执行间隔（秒）
ENFORCE_SECONDS = 1.0

_user32 = ctypes.windll.user32 if hasattr(ctypes, "windll") else None


def _acquire_singleton() -> "tuple[int, bool]":
    """申请命名互斥量，保证同一时间只有一个监控进程。

    返回 (句柄, 是否为本进程新建)。已存在时句柄无效，调用方直接退出即可。

    用 `Local\\` 前缀而不是 `Global\\`：只要求当前登录会话内唯一，
    不跨用户会话，免得普通权限去碰全局命名空间被拒。
    """
    if sys.platform != "win32":
        return 0, True
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        handle = kernel32.CreateMutexW(None, True, "Local\\CloudSync.Supervisor")
        ERROR_ALREADY_EXISTS = 183
        already = kernel32.GetLastError() == ERROR_ALREADY_EXISTS
        return int(handle or 0), not already
    except Exception:  # noqa: BLE001 - 拿不到互斥量就退化成"允许多实例"
        return 0, True


def _start_worker() -> int:
    """拉起一个悬浮窗进程，返回它的 pid。失败返回 0。"""
    runtime.prime_child_env()
    try:
        proc = subprocess.Popen(
            [runtime.app_exe(), "--worker"],
            cwd=runtime.app_dir(),
            creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except Exception:  # noqa: BLE001
        return 0
    return int(proc.pid)


# ---------------------------------------------------------------------------
# 可见性兜底：从进程外部把悬浮窗按回最前面
# ---------------------------------------------------------------------------
SW_SHOW = 5
SW_HIDE = 0
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
HWND_TOPMOST = -1


def _find_window_of(pid: int) -> int:
    """按进程枚举顶层窗口——**只用于诊断**，不再用于动手。

    同一个进程名下不只有悬浮窗：onefile 引导进程会留一块 1280×735 的
    "白板"窗口，Qt 还有自己的辅助窗口、输入法窗口。
    早先兜底逻辑就是靠这里猜窗口的，结果每秒钟去顶那块白板：
    白板被拽出来赖在任务栏上，悬浮窗被它挤下去再顶回来，界面就一闪一闪。

    所以动手一律走 `_registered_window()`：窗口自己登记句柄，外部只认登记。
    """
    if not _user32:
        return 0
    found = {"visible": 0, "any": 0}

    def callback(hwnd, _lparam):
        owner = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if int(owner.value) != pid:
            return True
        if not found["any"]:
            found["any"] = int(hwnd)
        if _user32.IsWindowVisible(hwnd):
            found["visible"] = int(hwnd)
            return False
        return True

    _user32.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND,
                                           wintypes.LPARAM)(callback), 0)
    return found["visible"] or found["any"]


def _registered_window() -> int:
    """取出悬浮窗**登记过的**句柄，并核对它现在确实属于悬浮窗进程。

    三道校验，缺一不可：
      1) 文件里有句柄
      2) 这个 hwnd 还是一个真实存在的窗口
      3) 它所属的 pid 等于 pid 文件里记的悬浮窗进程
    第 3 条最关键：窗口退出后句柄会被系统回收、数字可能被后来的窗口复用，
    不核对 pid 就可能把别的窗口当成悬浮窗去操作。
    """
    hwnd = runtime.read_window_handle()
    if not hwnd or not _user32:
        return 0
    if not _user32.IsWindow(wintypes.HWND(hwnd)):
        return 0
    owner = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(owner))
    expected = shutdown.read_pid()
    if not expected or int(owner.value) != int(expected):
        return 0
    return hwnd


def _on_any_screen(hwnd: int) -> bool:
    """窗口是否还和某块屏幕的可视区域有交集（没跑到屏幕外面去）。

    用 ctypes 直接枚举显示器，不引入 pywin32——这个进程必须只依赖标准库，
    否则"最兜底的一层"自己先缺胳膊少腿了。
    """
    rect = wintypes.RECT()
    if not _user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        return True
    left, top, right, bottom = rect.left, rect.top, rect.right, rect.bottom
    if right <= left or bottom <= top:
        return True
    hits = {"any": False}

    class _MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD),
                    ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD)]

    MONITOR_DEFAULTTONEAREST = 2

    def callback(hmon, _hdc, _lprc, _lparam):
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        if ctypes.windll.user32.GetMonitorInfoW(
                wintypes.HANDLE(hmon), ctypes.byref(info)):
            work = info.rcWork
            if left < work.right and right > work.left and \
                    top < work.bottom and bottom > work.top:
                hits["any"] = True
                return False
        return True

    _user32.EnumDisplayMonitors(
        None, None,
        ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HMONITOR, wintypes.HDC,
                           ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)(callback),
        0)
    return hits["any"]


def _center_on_primary(hwnd: int) -> None:
    """把窗口挪回主屏中间。"""
    SM_CXSCREEN, SM_CYSCREEN = 0, 1
    width = int(_user32.GetSystemMetrics(SM_CXSCREEN))
    height = int(_user32.GetSystemMetrics(SM_CYSCREEN))
    if width <= 0 or height <= 0:
        return
    rect = wintypes.RECT()
    if not _user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        return
    win_w = max(rect.right - rect.left, 200)
    win_h = max(rect.bottom - rect.top, 150)
    _user32.MoveWindow(wintypes.HWND(hwnd), (width - win_w) // 2,
                       (height - win_h) // 2, win_w, win_h, True)


def enforce_visibility(child_pid: int) -> str:
    """外部强制维持悬浮窗可见且在最前。返回本次做了什么（空串=什么都没做）。

    **这是整套方案里最兜底的一层，也是用户那句"最兜底的方法"的答案。**

    为什么不能靠程序自己保证：悬浮窗会被很多"自己没动手"的东西弄没——
      · 别的程序进全屏/专注模式，重排 z 序，把它压到下面
      · 独占全屏切分辨率，窗口落到可见区域之外
      · 别的程序（某些国产软件的"沉浸模式""专注模式"）直接调 ShowWindow 藏掉
        所有非顶层窗口
      · 程序自身某条分支出了异常，窗口停在隐藏状态没人复位
    这些都没走程序自己的代码，所以程序内部怎么写都兜不住。

    于是把这件事交给**进程外部**来做：本进程是纯 Win32 循环，不依赖 PyQt、
    不依赖悬浮窗进程里的任何状态，哪怕那个进程已经卡死、逻辑全乱，
    每秒一次的 ShowWindow / SetWindowPos 依然由系统直接执行。

    唯一会放行的情况：程序自己登记过"我是主动藏的"（用户按了隐藏键，
    或者检测到录屏风险）。那种情况要把窗口放回去反而是添乱，
    由 runtime.HIDDEN_FLAG 这个标记来区分。
    """
    if not _user32 or not child_pid:
        return ""
    if runtime.is_hidden_intentionally():
        return ""
    hwnd = _registered_window()
    if not hwnd:
        return ""

    _user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND,
                                     ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, wintypes.UINT]
    _user32.SetWindowPos.restype = ctypes.c_bool

    if not _user32.IsWindowVisible(wintypes.HWND(hwnd)):
        # 不知被谁藏了：显示回来（SW_SHOW 不抢焦点）
        _user32.ShowWindow(wintypes.HWND(hwnd), SW_SHOW)
        _user32.SetWindowPos(wintypes.HWND(hwnd), wintypes.HWND(HWND_TOPMOST),
                             0, 0, 0, 0,
                             SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
        runtime.log_event(f"兜底：窗口 hwnd={hwnd} 被外部藏了，已强制显示并置顶")
        return "shown"

    # 已可见：每次都重新声明一次置顶，别的程序抢了就压回去。
    # 代价只有一次 SetWindowPos（约几十微秒），换来任何全屏切换后都立刻回到最前。
    _user32.SetWindowPos(wintypes.HWND(hwnd), wintypes.HWND(HWND_TOPMOST),
                         0, 0, 0, 0,
                         SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
    if not _on_any_screen(hwnd):
        # 跑到所有屏幕外面了（多半是独占全屏改了分辨率），挪回主屏中间
        _center_on_primary(hwnd)
        runtime.log_event("兜底：窗口跑到屏幕外，已挪回主屏")
        return "moved"
    return "topmost"


def run() -> int:
    """监控主循环。返回进程退出码。"""
    handle, is_new = _acquire_singleton()
    if not is_new:
        # 已经在监控了：用户重复双击，交给在跑的那个实例，这里直接收工。
        runtime.log_event("已在监控，本次启动放弃")
        if handle:
            import ctypes
            ctypes.windll.kernel32.CloseHandle(handle)
        return 0

    # 用户此刻是主动双击了图标，意图就是"让它跑起来"：
    # 上一次退出留下的标记必须作废，否则会直接退出，什么都不发生。
    runtime.clear_user_exit()
    runtime.write_supervisor_pid()
    runtime.log_event("监控启动，接管保活")

    child_pid = 0
    started_at = 0.0
    fast_failures = 0
    last_enforce = 0.0
    try:
        child_pid = _start_worker()
        started_at = time.monotonic()
        runtime.log_event(f"已拉起悬浮窗 pid={child_pid or '失败'}")
        while True:
            if runtime.user_exited():
                runtime.log_event("看到用户主动退出标记，收工")
                break
            if not pid_alive(child_pid):
                # 非用户主动退出：收拾干净再拉起来
                from .shutdown import kill_orphan_proxies
                runtime.log_event(f"悬浮窗 pid={child_pid} 消失，准备拉起")
                kill_orphan_proxies()
                if runtime.user_exited():
                    runtime.log_event("清代理期间出现退出标记，收工")
                    break
                if time.monotonic() - started_at < MIN_HEALTHY_SECONDS:
                    fast_failures += 1
                else:
                    fast_failures = 0
                if fast_failures >= MAX_FAST_FAILURES:
                    fast_failures = 0
                    runtime.log_event(f"连续快速失败，休息 {COOLDOWN_SECONDS:.0f}s")
                    time.sleep(COOLDOWN_SECONDS)
                else:
                    time.sleep(1.0)
                if runtime.user_exited():
                    runtime.log_event("等待期间出现退出标记，收工")
                    break
                child_pid = _start_worker()
                started_at = time.monotonic()
                runtime.log_event(f"已重新拉起悬浮窗 pid={child_pid or '失败'}")
            # 可见性兜底：不管进程死没死，每秒都把窗口按回最前面
            now = time.monotonic()
            if now - last_enforce >= ENFORCE_SECONDS:
                last_enforce = now
                try:
                    # 每轮顺手清一遍 onefile 引导窗口：悬浮窗重启后会多出一块
                    runtime.hide_bootloader_windows()
                    enforce_visibility(child_pid)
                except Exception as exc:  # noqa: BLE001
                    runtime.log_event(f"可见性兜底异常（忽略）：{exc!r}")
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        pass
    finally:
        _cleanup(handle, child_pid)
    return 0


def _cleanup(handle: int, child_pid: int) -> None:
    """监控退场时的收尾。"""
    from . import shutdown
    if pid_alive(child_pid):
        shutdown._terminate(child_pid)  # noqa: SLF001 - 同一包内的兜底强杀
        runtime.log_event(f"收尾时结束残留的悬浮窗 pid={child_pid}")
    runtime.remove_supervisor_pid()
    shutdown.cleanup()
    if handle:
        try:
            import ctypes
            ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    runtime.silence_stdio()
    raise SystemExit(run())