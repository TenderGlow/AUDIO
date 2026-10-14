"""打包入口：只负责把参数交给分发器。

PyInstaller 会把本文件当 `__main__` 执行，所以不能直接写包内相对导入
（`from . import runtime` 在顶层脚本里会 ImportError）。真正的角色分发
在 `cloudSyncG/entry.py` 里。
"""
from cloudSyncG.entry import main

if __name__ == "__main__":
    raise SystemExit(main())