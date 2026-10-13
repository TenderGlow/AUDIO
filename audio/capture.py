"""整屏抓取：按"鼠标所在的那块显示器"抓取 → 压缩 → base64。

多显示器场景下只抓当前屏而不是整个虚拟桌面：双屏拼成的整屏图在缩放到 1600px 后，
每块屏幕只剩一半宽度，题目像素变少会明显影响识别率。
"""
from __future__ import annotations

import base64
import ctypes
import io
import os
import tempfile
import time
from ctypes import wintypes

from PIL import ImageGrab
from PyQt5.QtCore import QObject, QRunnable, pyqtSignal

MAX_WIDTH = 1600
QUALITY = 82

MONITOR_DEFAULTTOPRIMARY = 1


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)]


def cursor_monitor_rect() -> tuple[int, int, int, int] | None:
    """鼠标当前所在显示器的物理像素矩形；取不到就返回 None（退回整屏抓取）。"""
    try:
        user32 = ctypes.windll.user32
        point = wintypes.POINT()
        if not user32.GetCursorPos(ctypes.byref(point)):
            return None
        handle = user32.MonitorFromPoint(point, MONITOR_DEFAULTTOPRIMARY)
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        if not user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            return None
        rect = info.rcMonitor
        return rect.left, rect.top, rect.right, rect.bottom
    except Exception:  # noqa: BLE001 - 取不到就按"抓整屏"处理
        return None


class Capture:
    def __init__(self) -> None:
        self.path: str = ""
        self.width: int = 0
        self.height: int = 0
        self.base64: str = ""

    def grab(self, max_width: int = MAX_WIDTH, quality: int = QUALITY,
             current_monitor_only: bool = True) -> "Capture":
        bbox = cursor_monitor_rect() if current_monitor_only else None
        image = ImageGrab.grab(bbox=bbox, all_screens=True).convert("RGB")
        self.width, self.height = image.size
        if image.width > max_width:
            ratio = max_width / float(image.width)
            image = image.resize((max_width, max(1, int(image.height * ratio))))

        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=quality)
        self.base64 = base64.b64encode(buffer.getvalue()).decode("ascii")

        self.path = os.path.join(tempfile.gettempdir(),
                                 f"audio_{int(time.time() * 1000)}.jpg")
        image.save(self.path, format="JPEG", quality=quality)
        return self

    def cleanup(self) -> None:
        if self.path and os.path.exists(self.path):
            try:
                os.remove(self.path)
            except OSError:
                pass


class CaptureSignals(QObject):
    """抓屏在子线程完成，通过信号把结果送回主线程。"""

    done = pyqtSignal(object)      # Capture
    failed = pyqtSignal(str)


class CaptureWorker(QRunnable):
    """把"截图 + 缩放 + JPEG 编码 + base64"整包丢到线程池。

    这套流程实测约 100ms，放在主线程会让界面明显卡一下，
    所以必须离开 UI 线程。
    """

    def __init__(self, current_monitor_only: bool = True):
        super().__init__()
        self.signals = CaptureSignals()
        self.current_monitor_only = current_monitor_only

    def run(self) -> None:
        try:
            self.signals.done.emit(Capture().grab(
                current_monitor_only=self.current_monitor_only))
        except Exception as exc:  # noqa: BLE001 - 统一转成界面提示
            self.signals.failed.emit(str(exc))