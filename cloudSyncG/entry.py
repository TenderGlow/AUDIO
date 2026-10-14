"""打包后的 exe 入口：按参数决定这次进程扮演什么角色。

用户双击 `CloudSync.exe`（不带参数）时进的是**监控模式**，它只负责保活；
带 `--worker` 才是真正弹悬浮窗的进程；`--proxy` 是本地回环代理。
父进程、子进程、代理进程用的是同一个 exe，所以任务管理器里看到的
全是 CloudSync.exe，线程描述也统一叫 CloudSync。

为什么不用 `python -m cloudSyncG.proxy` 那套写法：冻成 exe 之后没有
`__main__` 模块可执行，必须让同一个二进制自己按参数分角色。
"""
from __future__ import annotations

import sys

from . import runtime


def _run_cli(action: str) -> int:
    """`--status / --stop / --kill`：从命令行查状态、停程序。

    这些是有回显的命令行工具，借父进程的控制台来打印；双击时借不到，
    什么都不输出是预期行为。
    """
    runtime.attach_parent_console()
    from . import shutdown

    if action == "status":
        info = shutdown.status()
        print(f"状态  : {'运行中' if info['running'] else '未运行'}")
        print(f"pid   : {info['pid'] or '-'}")
        print(f"hwnd  : {info['hwnd'] or '-'}")
        return 0 if info["running"] else 1
    if action == "stop":
        print("已优雅停止。" if shutdown.stop() else "未找到运行中的实例。")
        return 0
    if action == "kill":
        print("已强制结束。" if shutdown.kill() else "未找到运行中的实例。")
        return 0
    return 2


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    args = argv[1:]

    # 先把 stdio 接好：没有控制台时 sys.stdout 是 None，
    # 下面任何一处 print 都会直接抛异常把进程带走。
    runtime.silence_stdio()
    runtime.set_process_name()
    runtime.install_crash_log()

    role = args[0] if args else ""
    runtime.ROLE = {"--worker": "window", "--supervisor": "watchdog",
                    "--proxy": "proxy", "--status": "cli",
                    "--stop": "cli", "--kill": "cli"}.get(role, "watchdog")
    # 打包后每个进程前面都有一层 onefile 引导器，它会留下一块可见的
    # 1280×735 "白板"窗口（还没有 WS_EX_TOOLWINDOW，会进任务栏）。
    # 第一件事就是把它藏掉，否则双击时会先蹦出一块白板。
    runtime.hide_bootloader_windows()

    if role == "--worker":
        from .app import main as app_main
        return app_main()
    if role == "--supervisor":
        from .supervisor import run
        return run()
    if role == "--proxy":
        from .proxy import main as proxy_main
        return proxy_main(args[1:])
    if role in ("--status", "--stop", "--kill"):
        return _run_cli(role[2:])
    # 不带参数 = 用户双击：起监控，由它保活悬浮窗
    from .supervisor import run
    return run()


if __name__ == "__main__":
    raise SystemExit(main())