"""测试公用工具：清理残留进程 / 枚举本项目进程。

套件会互相干扰——上一个测试没清干净的 main.py 会让下一个测试的
"已注册热键"注册失败（RegisterHotKey 全局唯一）、并让"找哪个进程来杀"选错对象。
所以每个会启动真实实例的测试，开头都必须先调 `cleanup_all()`。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def list_project_procs():
    """返回 [(pid, cmdline)]，只含本项目的 python 进程。"""
    out = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" "
         "| Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"],
        capture_output=True, text=True, errors="replace").stdout.strip()
    rows = []
    if not out:
        return rows
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return rows
    if isinstance(data, dict):
        data = [data]
    for d in data:
        cmd = (d.get("CommandLine") or "")
        if "main.py" in cmd or "audio.proxy" in cmd:
            rows.append((int(d["ProcessId"]), cmd))
    return rows


def list_mains():
    return [r for r in list_project_procs() if "main.py" in r[1]]


def list_proxies():
    return [r for r in list_project_procs() if "audio.proxy" in r[1]]


def _taskkill(pid):
    subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                   capture_output=True)


def cleanup_all(verbose: bool = True) -> None:
    """干掉所有残留的本项目进程（含代理），并清掉 pid 文件。"""
    procs = list_project_procs()
    if not procs:
        return
    if verbose:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        print(f"        清理残留 {len(procs)} 个进程: "
              f"{[p for p, _ in procs]}")
    for pid, _cmd in procs:
        _taskkill(pid)
    # 等进程真正退出，最多 5 秒
    deadline = time.time() + 5
    while time.time() < deadline and list_project_procs():
        time.sleep(0.3)
    pid_file = os.path.join(os.environ.get("APPDATA", ""), "Audio", "audio.pid")
    try:
        os.remove(pid_file)
    except OSError:
        pass


def wait_for(predicate, timeout=40.0, interval=0.4):
    """轮询等待条件成立，返回是否成立。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()