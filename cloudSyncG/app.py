"""悬浮窗主进程（worker）：真正弹窗的那个。

源码直接跑和打包成 exe 走的是同一份代码，区别只在入口：
- 源码：`python cloudSyncG-main.py`
- exe：`CloudSync.exe --worker`（由监控进程拉起）

所以这里只放"干活"的逻辑，单实例判断、进程命名这类打包相关的动作放在最前面，
并且都在 Qt 初始化之前完成 —— 初始化之后再出问题，监控进程看到的就只是
"窗口没出来"，排查起来会多绕一层。
"""
from __future__ import annotations

import sys

from PyQt5.QtCore import Qt, QThreadPool
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QApplication

from . import proxy as proxy_mod
from . import runtime
from .config import load_settings
from .floating_window import FloatingWindow
from .shutdown import remove_pid_file, write_pid_file


def _acquire_singleton() -> bool:
    """同一时间只允许一个悬浮窗。返回 False 表示已有实例，本次直接退出。

    用命名互斥量而不是"pid 文件在不在"：pid 文件可能被清理工具删掉，
    互斥量则由系统在进程死亡的那一刻自动释放，不会留下假占用。
    """
    if sys.platform != "win32":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, True, "Local\\CloudSync.Worker")
        ERROR_ALREADY_EXISTS = 183
        return kernel32.GetLastError() != ERROR_ALREADY_EXISTS
    except Exception:  # noqa: BLE001
        return True


def _reveal(window) -> None:
    """让悬浮窗"先画好再露面"。

    分层窗口（WS_EX_LAYERED）在第一次 paint 之前，窗口内容其实是空的，
    屏幕上会先出现一块**白色实心板子**，等第一帧画上去才变成透明悬浮窗。
    实测启动时就能看到这半秒不到的一闪，像是多了个欢迎仪式。

    做法：先用 `WA_DontShowOnScreen` 让 Qt 完整画一遍（画到屏幕外），
    画完再撤掉这个属性真正显示——用户看到的第一帧就是最终形态，
    没有白板，也没有任何中间状态。
    """
    from PyQt5.QtCore import Qt

    window.setAttribute(Qt.WA_DontShowOnScreen, True)
    window.show()
    window.setAttribute(Qt.WA_DontShowOnScreen, False)
    window.show()


def main() -> int:
    runtime.set_process_name()
    if not _acquire_singleton():
        return 0

    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setApplicationName("CloudSync")
    app.setFont(QFont("Microsoft YaHei UI", 9))
    # 退出时最多等 2 秒让正在跑的流式请求收尾，避免线程被强杀导致崩溃
    app.aboutToQuit.connect(lambda: QThreadPool.globalInstance().waitForDone(2000))
    # 记录 pid：界面不进任务栏也不进托盘，外部要靠它才能把程序停掉
    write_pid_file()
    app.aboutToQuit.connect(remove_pid_file)

    settings = load_settings()

    # 本地回环代理跑在**独立进程**里：必须独立——进程网络栈互相隔离，
    # 代理若与主程序同进程，两者共用同一份连接表，主程序照样能看到
    # 到上游的那条连接，代理就白做了。
    proxy_proc = None
    if settings.use_local_proxy:
        base = f"http://{settings.proxy_host}:{settings.proxy_port}"
        proxy_proc = proxy_mod.spawn_detached(settings.proxy_host,
                                             settings.proxy_port)
        if proxy_proc is None or not proxy_mod.wait_until_ready(base, 8.0):
            # 代理起不来不能拖垮主程序：退回直连
            print("[cloudSyncG] 本地代理启动失败，本次退回直连")
            if proxy_proc is not None:
                try:
                    proxy_proc.terminate()
                except Exception:  # noqa: BLE001
                    pass
            proxy_proc = None
            settings.use_local_proxy = False
        else:
            settings.proxy_base = base

    def _stop_proxy():
        if proxy_proc is not None:
            try:
                proxy_proc.terminate()
                proxy_proc.wait(timeout=3)
            except Exception:  # noqa: BLE001
                try:
                    proxy_proc.kill()
                except Exception:  # noqa: BLE001
                    pass

    app.aboutToQuit.connect(_stop_proxy)

    window = FloatingWindow(settings)
    _reveal(window)
    return app.exec_()