"""程序入口：python main.py"""
from __future__ import annotations

import sys

from PyQt5.QtCore import Qt, QThreadPool
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QApplication

from audio.config import load_settings
from audio.floating_window import FloatingWindow
from audio.shutdown import write_pid_file, remove_pid_file
from audio import proxy as proxy_mod


def main() -> int:
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setApplicationName("Audio")
    app.setFont(QFont("Microsoft YaHei UI", 9))
    # 退出时最多等 2 秒让正在跑的流式请求收尾，避免线程被强杀导致崩溃
    app.aboutToQuit.connect(lambda: QThreadPool.globalInstance().waitForDone(2000))
    # 记录 pid：界面不进任务栏也不进托盘，外部要靠它才能把程序停掉
    write_pid_file()
    app.aboutToQuit.connect(remove_pid_file)

    settings = load_settings()

    # 本地回环代理：**独立进程**。
    # 必须独立——进程网络栈互相隔离，代理若与主程序同进程，两者共用同一份连接表，
    # 实测主程序照样能看到到上游的那条连接，代理就白做了。
    proxy_proc = None
    if settings.use_local_proxy:
        base = f"http://{settings.proxy_host}:{settings.proxy_port}"
        proxy_proc = proxy_mod.spawn_detached(settings.proxy_host,
                                             settings.proxy_port)
        if proxy_proc is None or not proxy_mod.wait_until_ready(base, 8.0):
            # 代理起不来不能拖垮主程序：退回直连
            print("[Audio] 本地代理启动失败，本次退回直连。")
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
    window.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())