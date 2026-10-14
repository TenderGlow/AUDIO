"""打包成 exe 之后才会出现的那些差异，全部集中收在这里。

源码直接 `python cloudSyncG-main.py` 跑的时候，这个文件基本什么都不用做；
一旦用 PyInstaller 冻成 `CloudSync.exe`，下面这些差异就都必须处理：

1. **没有控制台**：`--noconsole` 模式下 `sys.stdout` / `sys.stderr` 是 `None`，
   项目里到处 `print(...)`，不接管的话一打印就 AttributeError 直接崩。
2. **不能再用 `-m cloudSyncG.proxy` 起子进程**：冻成 exe 之后没有 `__main__` 可跑，
   只能让同一个 exe 换个参数进入"代理模式"。
3. **用户主动退出 vs 意外崩溃**：要区分这两种退出，监控进程才知道该不该拉起来。
   靠一个落盘标记（`user_exit.flag`）区分，而不是靠猜退出码。

另外负责两件小事：进程/线程命名，以及给子进程预置 PyInstaller 的复用环境变量。
"""
from __future__ import annotations

import os
import sys
import time

from .config import APP_DIR

# 进程与线程统一用的名字。任务管理器"映像名称/线程"列里看到的就是它。
PROCESS_NAME = "CloudSync"

# 本进程扮演的角色（监控 / 悬浮窗 / 代理），只写进崩溃日志便于排查
ROLE = "unknown"

# 用户**主动**点了悬浮窗退出（或外部执行了 stop）才会写下的标记。
# 监控进程只看这个标记决定"收工"还是"拉起来"，所以它必须由退出方亲手写。
EXIT_FLAG = os.path.join(APP_DIR, "user_exit.flag")
SUPERVISOR_PID_FILE = os.path.join(APP_DIR, "supervisor.pid")

# 窗口当前是"自己主动藏起来"的标记。
# 谁会主动藏：用户按 Ctrl+Alt+1、抓屏瞬间怕把自己截进去、检测到录屏风险。
# 这几种情况外部兜底监控**不能**把它强行拉回来，否则用户刚按了隐藏键
# 窗口又弹出来，等于这个功能失效了。写成文件就是为了让外部进程能区分
# "是自己藏的"（放行）和 "不知被谁弄没了"（拉回来）。
HIDDEN_FLAG = os.path.join(APP_DIR, "window_hidden.flag")

# 悬浮窗自己的窗口句柄。窗口**主动登记**自己的句柄，外部兜底监控只认这份登记。
#
# 为什么不靠"按进程枚举顶层窗口去找"：同一个进程名下不只有悬浮窗——
# PyInstaller 引导进程、Qt 自己的辅助窗口、输入法窗口全都在。
# 早先外部兜底就是靠枚举去找，结果**逮到了 onefile 引导那块 1280×735 的白板**，
# 每秒把它顶一次前：白板被拽出来常驻任务栏，悬浮窗被它挤下去又顶回来，
# 于是用户看到"白板赖着不走 + 悬浮窗一闪一闪"。
# 窗口自己报句柄，外部只认这份登记，从根上不会认错人。
WINDOW_HANDLE_FILE = os.path.join(APP_DIR, "window.hwnd")


def publish_window_handle(hwnd: int) -> None:
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(WINDOW_HANDLE_FILE, "w", encoding="ascii") as fp:
            fp.write(str(int(hwnd)))
    except (OSError, TypeError, ValueError):
        pass


def read_window_handle() -> int:
    try:
        with open(WINDOW_HANDLE_FILE, "r", encoding="ascii") as fp:
            return int(fp.read().strip())
    except (OSError, ValueError):
        return 0


def remove_window_handle() -> None:
    try:
        os.remove(WINDOW_HANDLE_FILE)
    except OSError:
        pass


def mark_hidden() -> None:
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(HIDDEN_FLAG, "w", encoding="ascii") as fp:
            fp.write(str(os.getpid()))
    except OSError:
        pass


def clear_hidden() -> None:
    try:
        os.remove(HIDDEN_FLAG)
    except OSError:
        pass


def is_hidden_intentionally() -> bool:
    return os.path.exists(HIDDEN_FLAG)


# ---------------------------------------------------------------------------
# PyInstaller onefile 引导窗口
# ---------------------------------------------------------------------------
BOOTLOADER_CLASS = "PyInstallerOnefileHiddenWindow"
# 引导窗口长这样：1280×735，正好是一个控制台窗口的默认尺寸，
# 窗口名里带"Hidden"但它**其实是被显示出来的**，而且没有 WS_EX_TOOLWINDOW，
# 于是会出现在任务栏里、可以被单独"关闭"——这就是用户看到的那块白板。
BOOTLOADER_SIZE = (1280, 735)


def hide_bootloader_windows() -> int:
    """把 onefile 引导进程留下的那块"白板"藏掉。返回处理了几个。

    为什么要专门做这件事：打包成 onefile 之后，每个进程前面都多一层
    引导器，它会创建一个同名的顶层窗口来压住控制台。本项目用无窗口子系统，
    控制台本来就不需要露脸，可这个窗口**恰恰是可见的**——启动时先蹦出
    一块 1280×735 的白色板子，悬浮窗正好落在它上面；任务栏里也能点到它，
    点"关闭窗口"白板就没了，悬浮窗还活着。

    这里按窗口类名精确匹配（这个类名是 PyInstaller 专有的），把它藏起来，
    再补上 WS_EX_TOOLWINDOW，确保它永远不会再出现在任务栏里。
    只碰"我们自己 exe 的"窗口，绝不误伤别的 PyInstaller 程序。
    """
    if sys.platform != "win32":
        return 0
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:  # noqa: BLE001
        return 0

    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        me = os.getpid()
        image = (os.path.basename(sys.executable) if is_frozen()
                 else os.path.basename(sys.argv[0] or "")).lower()
        target_names = {image, "cloudsync.exe", "python.exe", "pythonw.exe"}
        fixed = 0

        def callback(hwnd, _lparam):
            nonlocal fixed
            buf = ctypes.create_unicode_buffer(256)
            try:
                user32.GetClassNameW(hwnd, buf, 256)
            except Exception:  # noqa: BLE001
                return True
            if buf.value != BOOTLOADER_CLASS:
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            owner = int(pid.value)
            if owner == me:
                name = image
            else:
                handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION,
                                              False, owner)
                if not handle:
                    return True
                try:
                    size = wintypes.DWORD(512)
                    path = ctypes.create_unicode_buffer(size.value)
                    if not kernel32.QueryFullProcessImageNameW(
                            handle, 0, path, ctypes.byref(size)):
                        return True
                    name = path.value.rsplit("\\", 1)[-1].lower()
                finally:
                    kernel32.CloseHandle(handle)
            if name not in target_names:
                return True

            # 已经藏好、并且带上了工具窗口标志，就别再动它——
            # 这个函数每秒都会被调用一次，不做这个判断的话日志会被刷爆。
            ex = user32.GetWindowLongW(wintypes.HWND(hwnd), GWL_EXSTYLE)
            visible = bool(user32.IsWindowVisible(wintypes.HWND(hwnd)))
            if not visible and (ex & WS_EX_TOOLWINDOW):
                return True

            user32.ShowWindow(wintypes.HWND(hwnd), SW_HIDE)
            user32.SetWindowLongW(wintypes.HWND(hwnd), GWL_EXSTYLE,
                                  ex | WS_EX_TOOLWINDOW)
            fixed += 1
            return True

        user32.EnumWindows(ctypes.WINFUNCTYPE(
            ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)(callback), 0)
        if fixed:
            log_event(f"藏掉 {fixed} 个 onefile 引导窗口（白板）")
        return fixed
    except Exception:  # noqa: BLE001 - 藏不掉也不影响功能
        return 0


SW_HIDE = 0
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def is_frozen() -> bool:
    """是否跑在 PyInstaller 冻出来的 exe 里。"""
    return bool(getattr(sys, "frozen", False))


def app_exe() -> str:
    """当前"程序本体"的可执行文件路径。

    源码运行时返回 python 解释器；冻成 exe 后返回 exe 自己。
    所有子进程都以它为模板再挂参数启动，所以进程名天然全部一致。
    """
    if is_frozen():
        return sys.executable
    return sys.executable


def app_dir() -> str:
    """程序文件所在目录（子进程的工作目录）。"""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# 控制台
# ---------------------------------------------------------------------------
def attach_parent_console() -> None:
    """把调用方(cmd)的控制台借过来。

    只有 `--status / --stop / --kill` 这几个命令行模式用得到：
    它们本来就该在 cmd 窗口里看到回显，而 exe 本身是无窗口子系统，
    双击运行时没有控制台、借也借不到，直接忽略即可。
    """
    if not is_frozen():
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        ATTACH_PARENT_PROCESS = 0xFFFFFFFF
        if not kernel32.AttachConsole(ATTACH_PARENT_PROCESS):
            return
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace")
        sys.stderr = open("CONOUT$", "w", encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - 借不到控制台不影响功能
        pass


def silence_stdio() -> None:
    """没有控制台时，把 stdout/stderr 接到空设备。

    **必须在任何 print 之前调用**。项目里 print 用得很勤（config 加载、
    代理启动失败提示等），sys.stdout 是 None 时 print 会直接抛
    AttributeError —— 那才是"双击 exe 一闪而过"的真正原因之一。
    """
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            try:
                setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# 进程 / 线程命名
# ---------------------------------------------------------------------------
def set_thread_description(description: str = PROCESS_NAME) -> None:
    """给当前线程起名（Win10 1607+）。

    线程真正的"名字"在 Windows 上不可改，能改的是任务管理器详情页里显示的
    Description 列；`SetThreadDescription(NULL)` 作用于主线程。
    老系统没有这个 API，忽略即可。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.kernel32.SetThreadDescription(
            ctypes.c_void_p(0), ctypes.c_wchar_p(description))
    except Exception:  # noqa: BLE001 - 老系统不支持
        pass


def set_process_name(description: str = PROCESS_NAME) -> None:
    """给主线程打上进程级标签。

    任务管理器"映像名称"列取的是 exe 文件名，由 PyInstaller 的输出名决定；
    这一句补的是"说明"那一列。两者一起看，父进程、子进程、代理全叫 CloudSync。
    """
    set_thread_description(description)


# ---------------------------------------------------------------------------
# 子进程环境
# ---------------------------------------------------------------------------
def prime_child_env() -> None:
    """起子进程前的环境准备：把 PyInstaller 的私有变量清干净。

    **这里曾经想省掉重复解包，后来去掉了**：PyInstaller 的 onefile 引导器支持
    用 `_PYI_PARENT_PROCESS_LEVEL` / `_PYI_APPLICATION_HOME_DIR` 让子进程复用
    父进程已经解包好的目录，能省下每次几秒的解包时间。本程序是
    「监控 → 悬浮窗 → 代理」三层，超过了它支持的层级——从 6.22.1 起引导器加了
    安全校验，子进程会被判成 `Invalid parent process level: 2`，
    然后弹一个错误框、进程直接退出。

    所以这里只负责把可能从更外层继承来的私有变量清掉，让每个子进程都作为
    独立的顶层实例启动：多花一点解包时间，换来官方支持、不弹框、
    不受 PyInstaller 版本行为变化影响。
    """
    if not is_frozen():
        return
    for name in ("_PYI_PARENT_PROCESS_LEVEL", "_PYI_APPLICATION_HOME_DIR"):
        if name in os.environ:
            try:
                os.environ.pop(name, None)
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# 运行日志
# ---------------------------------------------------------------------------
WATCHDOG_LOG = os.path.join(APP_DIR, "watchdog.log")
# 日志上限：超过就砍掉前半截。一个静默程序如果出了问题，
# 这个文件是唯一的线索，但它自己也不能无限长。
_LOG_MAX_BYTES = 256 * 1024


def log_event(message: str) -> None:
    """往 `watchdog.log` 追加一行：这个进程这一刻做了什么决定。

    为什么需要：这是个**没有任何界面**的程序——不在任务栏、不在托盘、不弹框。
    出问题时用户能看到的现象只有"它又自己弹出来了"或者"它怎么不出来了"，
    光看现象没法区分是哪一环做的决定。这里把监控的每一次判断都记下来，
    保活逻辑一旦出问题，日志里能直接看出它当时看见了什么、为什么这么做。

    只在 frozen 模式下写：源码运行时 stdout 就够用了，不必多一个文件。
    """
    if not is_frozen():
        return
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        if os.path.exists(WATCHDOG_LOG) and \
                os.path.getsize(WATCHDOG_LOG) > _LOG_MAX_BYTES:
            with open(WATCHDOG_LOG, "r", encoding="utf-8", errors="replace") as fp:
                tail = fp.readlines()[-400:]
            with open(WATCHDOG_LOG, "w", encoding="utf-8") as fp:
                fp.writelines(tail)
        with open(WATCHDOG_LOG, "a", encoding="utf-8") as fp:
            fp.write(f"{time.strftime('%H:%M:%S')} pid={os.getpid():<6} "
                     f"{ROLE:<8} {message}\n")
    except Exception:  # noqa: BLE001 - 写日志失败绝不能影响主流程
        pass


# ---------------------------------------------------------------------------
# 崩溃日志
# ---------------------------------------------------------------------------
def install_crash_log() -> None:
    """未捕获异常写进 `%APPDATA%\\cloudSyncG\\error.log`，不弹任何框。

    这个程序是"双击就静默跑起来"的形态，弹一个 PyInstaller 的错误框既没人
    会看，也会挡住桌面。写日志就够了：真出问题时那个文件里一定有栈。
    也不把异常交回默认处理器——交给它就会弹框，正是这里要避免的。
    """
    log_path = os.path.join(APP_DIR, "error.log")

    def _hook(exc_type, exc, tb):
        try:
            import traceback

            os.makedirs(APP_DIR, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as fp:
                fp.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} "
                         f"pid={os.getpid()} role={ROLE} ---\n")
                traceback.print_exception(exc_type, exc, tb, file=fp)
        except Exception:  # noqa: BLE001 - 记日志失败也不能再抛
            pass

    sys.excepthook = _hook


# ---------------------------------------------------------------------------
# 用户主动退出标记
# ---------------------------------------------------------------------------
def mark_user_exit() -> None:
    """标记"用户主动退出"——监控进程看到这个标记就不再拉起。"""
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(EXIT_FLAG, "w", encoding="ascii") as fp:
            fp.write(str(os.getpid()))
        log_event("写入用户主动退出标记")
    except OSError:
        pass


def clear_user_exit() -> None:
    """清掉标记。

    只在**用户新点了 exe**（即监控进程刚起来）时调用：用户主动点了图标，
    意图就是要跑起来，之前那次退出留下的标记不该继续生效。
    """
    try:
        os.remove(EXIT_FLAG)
        log_event("清除上次的退出标记")
    except OSError:
        pass


def user_exited() -> bool:
    return os.path.exists(EXIT_FLAG)


def read_supervisor_pid() -> int:
    try:
        with open(SUPERVISOR_PID_FILE, "r", encoding="ascii") as fp:
            return int(fp.read().strip())
    except (OSError, ValueError):
        return 0


def write_supervisor_pid() -> None:
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(SUPERVISOR_PID_FILE, "w", encoding="ascii") as fp:
            fp.write(str(os.getpid()))
    except OSError:
        pass


def remove_supervisor_pid() -> None:
    try:
        os.remove(SUPERVISOR_PID_FILE)
    except OSError:
        pass


__all__ = [
    "APP_DIR", "EXIT_FLAG", "HIDDEN_FLAG", "PROCESS_NAME", "ROLE",
    "SUPERVISOR_PID_FILE", "WATCHDOG_LOG", "WINDOW_HANDLE_FILE",
    "app_dir", "app_exe", "attach_parent_console", "clear_hidden",
    "clear_user_exit", "hide_bootloader_windows", "install_crash_log",
    "is_frozen", "is_hidden_intentionally", "log_event", "mark_hidden",
    "mark_user_exit", "prime_child_env", "publish_window_handle",
    "read_supervisor_pid", "read_window_handle", "remove_supervisor_pid",
    "remove_window_handle", "set_process_name", "set_thread_description",
    "silence_stdio", "user_exited", "write_supervisor_pid",
]