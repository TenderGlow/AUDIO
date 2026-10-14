# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：产出单文件 `CloudSync.exe`。

几个关键选项，以及为什么这么选：

- `console=False`（无窗口子系统）
  双击不会闪一个黑框。代价是 sys.stdout 是 None，项目里到处 print，
  所以入口第一件事就是 `runtime.silence_stdio()` 把它们接到空设备。

- `onefile`
  只留一个 exe，拷到哪台机器都能直接跑，不需要带一堆 DLL。

- 父/子进程是同一个 exe
  监控进程、悬浮窗进程、代理进程都是 CloudSync.exe 靠参数区分角色，
  任务管理器里看到的名字自然统一，不存在"一堆 python.exe"的观感。
"""
from pathlib import Path

# SPECPATH 是本 spec 所在目录（<项目根>/pack），它的上一级才是项目根
PROJECT = Path(SPECPATH).parent

# 项目里根本没用到的东西，排掉能省不少体积，也少一堆误报。
# 注意 email / json / ssl 这些**不能排**：`http.client`、`http.server` 在
# 顶层就 import email.message / email.utils，排掉的话代理进程直接起不来。
EXCLUDES = [
    "tkinter", "unittest", "pydoc", "doctest", "lib2to3",
    "numpy", "matplotlib", "pandas", "scipy", "IPython", "pytest",
    "setuptools", "pip", "sqlite3", "win32com",
]

a = Analysis(
    [str(PROJECT / "pack" / "launcher.py")],
    pathex=[str(PROJECT)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="CloudSync",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
    version=str(PROJECT / "pack" / "version_info.txt"),
)