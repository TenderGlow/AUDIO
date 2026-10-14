"""源码运行入口：python cloudSyncG-main.py

真正干活的代码在 `cloudSyncG/app.py`，这里只做一层转发，
好让"源码跑"和"exe 跑"共用同一份实现，不会各改各的。
"""
from __future__ import annotations

import sys

from cloudSyncG import runtime
from cloudSyncG.app import main

if __name__ == "__main__":
    runtime.silence_stdio()
    raise SystemExit(main())