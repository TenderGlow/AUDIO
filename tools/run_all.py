"""跑全部自检套件。

注意：不能一上来就对所有套件设 `QT_QPA_PLATFORM=offscreen`。
`verify_anticapture` / `verify_shutdown` 需要**真实** Windows 平台——
`SetWindowDisplayAffinity` 和 `RegisterHotKey` 在 offscreen 下是不工作的，
强行跑会得到一堆假失败。
"""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")

# (套件名, 是否需要 offscreen)
SUITES = [
    ("verify.py", True),
    ("verify_multipage.py", True),
    ("verify_proxy.py", True),
    ("verify_isolation.py", True),
    ("verify_orphan.py", True),
    ("verify_anticapture.py", False),   # 真实平台
    ("verify_shutdown.py", False),      # 真实平台
    ("verify_capture.py", False),       # 真实平台：像素级证明
]


def run(name: str, offscreen: bool) -> bool:
    env = dict(os.environ)
    env.pop("QT_QPA_PLATFORM", None)
    if offscreen:
        env["QT_QPA_PLATFORM"] = "offscreen"
    out = subprocess.run([sys.executable, "-u", os.path.join(TOOLS, name)],
                         cwd=ROOT, env=env, capture_output=True,
                         text=True, errors="replace")
    text = out.stdout + out.stderr
    summary = ""
    for line in text.splitlines():
        if "通过" in line and "失败" in line:
            summary = line.strip()
    mode = "offscreen" if offscreen else "real    "
    print(f"  {name:<26} [{mode}] exit={out.returncode}  {summary}")
    if out.returncode != 0:
        for line in text.splitlines():
            if "FAIL" in line or "Error" in line or "Traceback" in line:
                print(f"      {line.strip()}")
    return out.returncode == 0


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("=" * 66)
    print("运行全部自检套件")
    print("=" * 66)
    results = []
    for name, offscreen in SUITES:
        results.append((name, run(name, offscreen)))
    failed = [n for n, ok in results if not ok]
    print("-" * 66)
    print(f"套件 {len(results)} 个，通过 {len(results) - len(failed)}，"
          f"失败 {len(failed)}")
    if failed:
        print("失败：" + ", ".join(failed))
    print("=" * 66)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())