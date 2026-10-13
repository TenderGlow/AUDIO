"""一键备份 cloudSyncG 项目。

**在动任何代码之前先跑这个。** 2026-10-13 我因为新建的目录名和源码包名
在 Windows 上被当成同一个目录，一次目录操作误删了源码包里的源码文件，
不得不从零重建。这个教训的代价是整整一轮返工。

用法：
    python tools\backup.py            # 打一个带时间戳的 zip 快照
    python tools\backup.py --keep 10  # 只保留最近 10 份

快照会放到 `backups/` 下，并且**放在项目目录之外**（上一级），
避免备份和源码同处一棵树时，一起被误删。
"""
from __future__ import annotations

import datetime
import os
import shutil
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 放到项目外面：源码和备份不能住在同一棵目录树里
BACKUP_DIR = os.path.join(os.path.dirname(ROOT), "cloudSyncG备份")

INCLUDE_DIRS = ("cloudSyncG", "tools")
INCLUDE_FILES = ("cloudSyncG-main.py", "README.md", "requirements.txt",
                 "启动.bat", "开机自启.bat", "安装开机自启.bat",
                 "停止cloudSyncG.bat", "取消开机自启.bat")
EXCLUDE_DIRS = {"__pycache__", ".git", "backups"}


def make_backup(keep: int = 20) -> str:
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    target = os.path.join(BACKUP_DIR, f"cloudSyncG-{stamp}.zip")

    count = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in INCLUDE_DIRS:
            base = os.path.join(ROOT, name)
            if not os.path.isdir(base):
                continue
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
                for fn in filenames:
                    if fn.endswith((".pyc", ".pyo")):
                        continue
                    full = os.path.join(dirpath, fn)
                    rel = os.path.relpath(full, ROOT)
                    zf.write(full, rel)
                    count += 1
        for name in INCLUDE_FILES:
            full = os.path.join(ROOT, name)
            if os.path.isfile(full):
                zf.write(full, name)
                count += 1

    prune(keep)
    return target, count


def prune(keep: int) -> None:
    snaps = sorted(f for f in os.listdir(BACKUP_DIR) if f.endswith(".zip"))
    for old in snaps[:-keep] if keep > 0 else snaps:
        try:
            os.remove(os.path.join(BACKUP_DIR, old))
        except OSError:
            pass


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    keep = 20
    if "--keep" in sys.argv:
        try:
            keep = int(sys.argv[sys.argv.index("--keep") + 1])
        except (IndexError, ValueError):
            pass
    path, count = make_backup(keep)
    size = os.path.getsize(path) / 1024
    print(f"[备份完成] {count} 个文件 -> {path}  ({size:.0f} KB)")
    print(f"保留最近 {keep} 份，历史快照在：{BACKUP_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())