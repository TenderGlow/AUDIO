"""跑全部自检套件。

注意：不能一上来就对所有套件设 `QT_QPA_PLATFORM=offscreen`。
依赖 Win32 原生能力的套件在 offscreen 下**不会工作**，会得到一堆假失败：
  - verify_capture / verify_privacy：SetWindowDisplayAffinity 无效
  - verify_anticapture / verify_shutdown：需要真实窗口与 RegisterHotKey
`run_all.py` 已按套件分别处理。
"""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")

# (套件名, 是否用 offscreen)
SUITES = [
    ("verify.py", True),
    ("verify_buttons.py", True),
    ("verify_rendercache.py", True),
    ("verify_appearance.py", True),
    ("verify_noabort.py", True),
    ("verify_multipage.py", True),
    ("verify_proxy.py", True),
    ("verify_isolation.py", True),
    ("verify_orphan.py", True),
    ("verify_resize.py", True),
    ("verify_widgets.py", True),
    # 以下必须在真实 Windows 平台
    ("verify_privacy.py", False),
    ("verify_screen.py", False),     # 真实平台：抓屏专项复核
    ("verify_five.py", False),      # 真实平台：五项复核
    ("verify_anticapture.py", False),
    ("verify_shutdown.py", False),
    ("verify_capture.py", False),
]


def cleanup_between_suites() -> None:
    """每个套件跑之前，先清掉上一个套件可能留下的进程与 pid 文件。

    这些东西是**系统级共享**的：
      - 全局热键系统唯一，残留实例占着 Ctrl+Alt+0/1/Q，
        下一个套件的"热键已注册"就会假失败；
      - pid 文件共享，过期的 pid 会让停止逻辑瞄错目标。
    不清理的话，全套跑起来会冒出一堆"看着像回归、其实是脏状态"的失败。
    """
    try:
        sys.path.insert(0, TOOLS)
        import harness  # noqa: PLC0415
        harness.cleanup_all(verbose=False)
    except Exception as exc:  # noqa: BLE001
        print(f"      (suite cleanup skipped: {exc})")

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
        cleanup_between_suites()
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
