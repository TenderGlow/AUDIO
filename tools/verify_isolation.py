"""任务三决定性验证：代理拆成独立进程后，两边的连接表分别长什么样。

这是对用户所选目标（A：主程序那一行干净）的直接检验。
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

from PyQt5.QtWidgets import QApplication
app = QApplication(sys.argv)

from cloudSyncG.config import load_settings
from cloudSyncG import proxy as proxy_mod
from cloudSyncG.llm import stream_chunks
from cloudSyncG.capture import Capture

MY_PID = os.getpid()
ok = fail = 0


def check(label, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {label} {extra}")
    else:
        fail += 1
        print(f"  FAIL  {label} {extra}")


def sample_for(pid):
    """采样指定 pid 的 ESTABLISHED 连接，返回 [(local, remote), ...]"""
    out = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                         capture_output=True, text=True, errors="replace").stdout
    rows = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0].upper() == "TCP" and parts[4] == str(pid):
            if parts[3] == "ESTABLISHED":
                rows.append((parts[1], parts[2]))
    return rows


def is_loop(remote):
    return remote.startswith("127.0.0.1") or remote.startswith("[::1]")


class Watcher:
    def __init__(self, pid):
        self.pid = pid
        self.loopback, self.external = [], []
        self.running = True

    def poll(self):
        for _l, r in sample_for(self.pid):
            (self.loopback if is_loop(r) else self.external).append(r)


# 启动独立代理进程
proxy_proc = proxy_mod.spawn_detached("127.0.0.1", 8901)
check("独立代理进程已启动", proxy_proc is not None,
      f"-> pid {proxy_proc.pid if proxy_proc else '-'}")
check("代理健康检查通过",
      proxy_mod.wait_until_ready("http://127.0.0.1:8901", 10))

w_me = Watcher(MY_PID)
w_proxy = Watcher(proxy_proc.pid)

s = load_settings()
s.use_local_proxy = True
s.proxy_base = "http://127.0.0.1:8901"

cap = Capture().grab()
body = []
t0 = time.monotonic()
for kind, chunk in stream_chunks([cap.base64], s):
    w_me.poll()
    w_proxy.poll()
    if kind == "content":
        body.append(chunk)
cap.cleanup()
dt = time.monotonic() - t0

print("\n=== 主程序（本次测试进程）===")
print(f"  pid {MY_PID}")
print(f"  回环连接 {len(w_me.loopback)} 条，样例 {sorted(set(w_me.loopback))[:3]}")
print(f"  外网连接 {len(w_me.external)} 条，样例 {sorted(set(w_me.external))[:3]}")

print("\n=== 代理进程 ===")
print(f"  pid {proxy_proc.pid}")
print(f"  回环连接 {len(w_proxy.loopback)} 条")
print(f"  外网连接 {len(w_proxy.external)} 条，"
      f"样例 {sorted(set(w_proxy.external))[:3]}")

text = "".join(body)
print(f"\n正文 {len(text)} 字，耗时 {dt:.2f}s")
print(f"首行: {text.strip()[:60]!r}")

print("\n=== 判定 ===")
check("请求成功返回正文", len(text.strip()) > 0)
check("主程序**只有**回环连接，没有外连", len(w_me.external) == 0,
      "" if not w_me.external else f"-> 仍看到 {sorted(set(w_me.external))[:2]}")
check("主程序确实用了回环连接", len(w_me.loopback) > 0)
check("上游连接落在代理进程上", len(w_proxy.external) > 0,
      "→ 连接是隔离了，没有消失")

proxy_proc.terminate()
proxy_proc.wait(timeout=5)

print("\n" + "=" * 52)
print(f"通过 {ok} / 失败 {fail}")
print("=" * 52)
print()
print("结论：主程序的连接表现在只剩 127.0.0.1 —— 这是你要的效果。")
print("但上游那条连接搬到了代理进程，并没有从系统里消失。")
sys.exit(1 if fail else 0)