"""后台全局热键：不在界面上显示、不加任何提示说明。

默认 Ctrl+Alt+0 显示、Ctrl+Alt+1 隐藏、Ctrl+Alt+Q 彻底退出
（Ctrl+Alt 组合避免与 Chrome 的 Ctrl+1 切标签、Ctrl+0 恢复标签页等快捷键冲突）。
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

from PyQt5.QtCore import QObject, pyqtSignal

WM_HOTKEY = 0x0312
MOD_NOREPEAT = 0x4000
MOD_SHIFT = 0x0001
MOD_CONTROL = 0x0002
MOD_ALT = 0x0004

HOTKEY_SHOW = 1
HOTKEY_HIDE = 2
HOTKEY_QUIT = 3

_MODIFIERS = {"ctrl": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT}


def parse_hotkey(spec: str):
    """把 'Ctrl+Alt+0' 解析成 (modifiers, vk)。"""
    mods = MOD_NOREPEAT
    key = None
    for part in spec.replace(" ", "").split("+"):
        low = part.lower()
        if low in _MODIFIERS:
            mods |= _MODIFIERS[low]
        elif len(part) == 1:
            key = ord(part.upper())
    if key is None:
        raise ValueError(f"无法解析热键：{spec}")
    return mods, key


class _MSG(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND),
                ("message", wintypes.UINT),
                ("wParam", wintypes.WPARAM),
                ("lParam", wintypes.LPARAM),
                ("time", wintypes.DWORD),
                ("pt", wintypes.POINT)]


def message_fields(message):
    """从 Qt 传入的原生消息指针里取出 (message, wParam)。"""
    msg = ctypes.cast(int(message), ctypes.POINTER(_MSG)).contents
    return int(msg.message), int(msg.wParam)


def exclude_window_from_capture(hwnd: int) -> bool:
    """把窗口从屏幕捕获里排除（WDA_EXCLUDEFROMCAPTURE）。

    效果：本地照常可见，但不会被任何截图/录屏/屏幕共享（如 Chrome 的屏幕共享）
    收进去。实测：普通窗口设 0x11 后 BitBlt 抓到的像素从 40000 降到 0。
    """
    if not hwnd:
        return False
    try:
        ctypes.windll.user32.SetWindowDisplayAffinity(wintypes.HWND(hwnd), 0x11)
        result = wintypes.DWORD()
        ctypes.windll.user32.GetWindowDisplayAffinity(wintypes.HWND(hwnd),
                                                      ctypes.byref(result))
        return bool(result.value == 0x11)
    except Exception:  # noqa: BLE001 - 老系统不支持
        return False


def foreground_process_name() -> str:
    """当前前台窗口所属的进程名（小写，不含扩展名）。取不到就返回空串。"""
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(512)
            buf = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                name = buf.value.rsplit("\\", 1)[-1].lower()
                return name[:-4] if name.endswith(".exe") else name
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001 - 取不到就按"不是浏览器"处理
        return ""
    return ""


class HotkeyManager(QObject):
    """注册全局热键，消息由窗口的 nativeEvent 拦截。"""

    show_requested = pyqtSignal()
    hide_requested = pyqtSignal()
    quit_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hwnd = 0
        self._registered: list[tuple[int, str]] = []

    def bind(self, hwnd: int, show_spec: str, hide_spec: str,
             quit_spec: str = "") -> None:
        """幂等绑定：句柄和按键都没变就什么都不做。"""
        changed = bool(hwnd) and hwnd != self._hwnd
        if changed:
            self.unregister()
            self._hwnd = hwnd
        if not self._registered:
            self.register(show_spec, hide_spec, quit_spec)

    def register(self, show_spec: str, hide_spec: str, quit_spec: str = "") -> None:
        if not self._hwnd:
            return
        self.unregister()
        user32 = ctypes.windll.user32
        wanted = [(HOTKEY_SHOW, show_spec), (HOTKEY_HIDE, hide_spec)]
        if quit_spec:
            wanted.append((HOTKEY_QUIT, quit_spec))
        for hotkey_id, spec in wanted:
            try:
                mods, vk = parse_hotkey(spec)
            except ValueError:
                continue
            if user32.RegisterHotKey(wintypes.HWND(self._hwnd), hotkey_id, mods, vk):
                self._registered.append((hotkey_id, spec))

    def unregister(self) -> None:
        if self._hwnd:
            user32 = ctypes.windll.user32
            for hotkey_id, _spec in self._registered:
                user32.UnregisterHotKey(wintypes.HWND(self._hwnd), hotkey_id)
        self._registered.clear()

    def dispatch(self, hotkey_id: int) -> None:
        if hotkey_id == HOTKEY_SHOW:
            self.show_requested.emit()
        elif hotkey_id == HOTKEY_HIDE:
            self.hide_requested.emit()
        elif hotkey_id == HOTKEY_QUIT:
            self.quit_requested.emit()