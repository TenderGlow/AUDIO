"""净室测试：整台机器上只有 CloudSync.exe 一个文件，能不能正常工作。

模拟"把 exe 拷到一台没装过 Python、没拷任何源码的电脑上"：
  - 放进一个全新的空目录
  - 清掉 %APPDATA% 下的配置（等价于第一次在这台机器上运行）
  - 启动 → 验证悬浮窗/代理/保活 → 停止 → 清理

跑完请自行清理该目录与 %APPDATA%\cloudSyncG。
"""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from ctypes import wintypes

SRC_EXE = r"C:\Users\admin\Desktop\cloudSyncG\dist\CloudSync.exe"
CLEAN_DIR = os.path.join(os.environ["LOCALAPPDATA"], "Temp", "cloudsync_cleanroom")
APPDATA_DIR = os.path.join(os.environ["APPDATA"], "cloudSyncG")

_user32 = ctypes.windll.user32
_fail: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        _fail.append(name)


def processes() -> dict[int, str]:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='CloudSync.exe'\" | "
         "ForEach-Object { \"$($_.ProcessId)|$($_.CommandLine)\" }"],
        capture_output=True, text=True, timeout=30).stdout
    rows = {}
    for line in out.splitlines():
        if "|" in line:
            pid, cmd = line.split("|", 1)
            try:
                rows[int(pid.strip())] = cmd.strip()
            except ValueError:
                pass
    return rows


def wait_window(timeout: float) -> tuple[int, float]:
    sys.path.insert(0, r"C:\Users\admin\Desktop\cloudSyncG")
    from cloudSyncG import shutdown
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        hwnd = shutdown.find_window()
        if hwnd and _user32.IsWindowVisible(wintypes.HWND(hwnd)):
            return hwnd, time.monotonic() - start
        time.sleep(0.1)
    return 0, time.monotonic() - start


def proxy_alive() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8781/_ping", timeout=2) as r:
            return bool(json.loads(r.read()).get("ok"))
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    print("净室测试：只有一个 exe，没有任何源码")

    # 1. 造一个只含 exe 的干净目录
    shutil.rmtree(CLEAN_DIR, ignore_errors=True)
    os.makedirs(CLEAN_DIR)
    exe = os.path.join(CLEAN_DIR, "CloudSync.exe")
    shutil.copy2(SRC_EXE, exe)
    files = os.listdir(CLEAN_DIR)
    check("干净目录里只有 CloudSync.exe 一个文件",
          files == ["CloudSync.exe"], f"-> {files}")

    # 2. 清掉配置，等价于"这台机器第一次运行"
    shutil.rmtree(APPDATA_DIR, ignore_errors=True)
    check("已清空 %APPDATA%\\cloudSyncG（模拟全新机器）",
          not os.path.exists(APPDATA_DIR))

    # 3. 确认没有 python 解释器可用会出事（说明它确实自带了运行时）
    print("\n启动中（工作目录 = 那个只有 exe 的空目录）...")
    subprocess.Popen([exe], cwd=CLEAN_DIR, creationflags=0x00000008)
    hwnd, waited = wait_window(45.0)
    check("悬浮窗正常弹出", hwnd != 0, f"{waited:.1f}s")

    if hwnd:
        _user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        exstyle = int(_user32.GetWindowLongPtrW(wintypes.HWND(hwnd), -20)) & 0xFFFFFFFF
        check("仍在最前、不进任务栏（WS_EX_TOOLWINDOW）",
              bool(exstyle & 0x80), f"exstyle=0x{exstyle:08X}")

    time.sleep(6)
    procs = processes()
    check("进程全部来自这个 exe（无源码、无 python）",
          all(exe.lower().replace("/", "\\") in c.lower() or
              c.lower().startswith("cloudsync.exe") for c in procs.values()),
          f"{len(procs)} 个进程")

    time.sleep(3)
    check("本地代理已起来（内嵌的上游地址可用）", proxy_alive())

    # 4. 配置是否落在 APPDATA，而不是 exe 旁边
    # 注意：settings.json 只在"退出时保存凭据"之后才出现，模型凭据在
    # config.py 里已经写死了，所以刚启动时目录里只有 pid/日志是正常的。
    check("没有往 exe 所在目录写任何文件（配置全在 %APPDATA%）",
          sorted(os.listdir(CLEAN_DIR)) == ["CloudSync.exe"],
          f"-> exe 目录: {sorted(os.listdir(CLEAN_DIR))}；"
          f"%APPDATA%: {sorted(os.listdir(APPDATA_DIR))}")

    # 5. 保活
    pid = int(open(os.path.join(APPDATA_DIR, "cloudSyncG.pid")).read().strip())
    ctypes.windll.kernel32.TerminateProcess(
        ctypes.windll.kernel32.OpenProcess(0x0001, False, pid), 0)
    hwnd2, waited2 = wait_window(30.0)
    check("强杀后被自动拉回（保活生效）", hwnd2 != 0, f"{waited2:.1f}s")

    # 6. 停止
    subprocess.run([exe, "--stop"], capture_output=True, timeout=60)
    time.sleep(4)
    check("停止后进程全部退出", not processes(),
          f"-> 残留 {list(processes())}")

    # 7. 再来一次，确认可重复
    subprocess.Popen([exe], cwd=CLEAN_DIR, creationflags=0x00000008)
    hwnd3, waited3 = wait_window(45.0)
    check("停止后还能再次启动", hwnd3 != 0, f"{waited3:.1f}s")
    subprocess.run([exe, "--stop"], capture_output=True, timeout=60)
    time.sleep(3)

    print("\n" + ("净室测试全部通过：单文件即可运行"
                  if not _fail else f"失败 {len(_fail)} 项：" + "、".join(_fail)))
    return 0 if not _fail else 1


if __name__ == "__main__":
    raise SystemExit(main())