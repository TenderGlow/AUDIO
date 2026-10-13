"""任务三核心验证：直接观察**本进程真实建立**的 TCP 连接。

做法：在同一个进程里发起真实请求，同时用 netstat 采样自己的 pid。
本进程长驻，采样不会漏；比"从外面轮询子进程"可靠得多。
"""
import json
import os
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

from PyQt5.QtWidgets import QApplication
app = QApplication(sys.argv)

from audio.config import load_settings
from audio import proxy as proxy_mod
from audio.llm import stream_chunks
from audio.capture import Capture

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


def sample():
    """采样一次自己的 TCP 连接，返回 [(local, remote), ...]

    netstat -ano 的列是：协议 / 本地地址 / 外部地址 / 状态 / PID
    注意 PID 是**第 5 列（索引 4）**，索引 3 是状态——之前写错成 3，导致一条都没匹配上。
    """
    out = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                         capture_output=True, text=True, errors="replace").stdout
    rows = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0].upper() == "TCP" and parts[4] == str(MY_PID):
            if parts[3] == "ESTABLISHED":
                rows.append((parts[1], parts[2]))
    return rows


def is_loop(remote):
    return remote.startswith("127.0.0.1") or remote.startswith("[::1]")


class Watcher(threading.Thread):
    """后台持续采样连接表，直到被叫停。"""

    def __init__(self):
        super().__init__(daemon=True)
        self.stop_flag = threading.Event()
        self.loopback = []      # 采样到的回环连接
        self.external = []      # 采样到的外网连接
        self.samples = 0

    def run(self):
        while not self.stop_flag.is_set():
            for l, r in sample():
                if is_loop(r):
                    self.loopback.append(r)
                else:
                    self.external.append(r)
            self.samples += 1
            time.sleep(0.03)


def run_case(label, use_proxy):
    w = Watcher()
    w.start()
    time.sleep(0.3)

    s = load_settings()
    c = Capture().grab()
    text = []
    if use_proxy:
        srv = proxy_mod.ProxyServer("127.0.0.1", 0).start()
        s.use_local_proxy = True
        s.proxy_base = srv.base_url
        for k, ch in stream_chunks([c.base64], s):
            if k == "content":
                text.append(ch)
        srv.stop()
    else:
        s.use_local_proxy = False
        for k, ch in stream_chunks([c.base64], s):
            if k == "content":
                text.append(ch)
    c.cleanup()

    time.sleep(0.3)
    w.stop_flag.set()
    w.join(timeout=3)

    body = "".join(text)
    print(f"\n--- {label} ---")
    print(f"  采样次数 {w.samples}")
    print(f"  回环连接样本 {len(w.loopback)} 个，例如 {sorted(set(w.loopback))[:2]}")
    print(f"  外网连接样本 {len(w.external)} 个，例如 {sorted(set(w.external))[:2]}")
    print(f"  正文 {len(body)} 字")
    return w, body


print("=== A. 直连模式 ===")
w1, body1 = run_case("直连", False)
check("直连能拿到正文", len(body1.strip()) > 0)
check("直连时存在外网连接", len(w1.external) > 0,
      "→ 这正是代理要消除的那条连接")
if w1.external:
    print(f"        外网远端示例: {sorted(set(w1.external))[0]}")

print("\n=== B. 代理模式 ===")
w2, body2 = run_case("经本地代理", True)
check("经代理能拿到正文", len(body2.strip()) > 0)
check("经代理建立了回环连接", len(w2.loopback) > 0)

print("\n=== 结论（据实说明）===")
print("  · 直连：主进程持有到公网的连接")
print("  · 代理：主程序这层只有回环连接")
if w2.external:
    print("  · 但本进程里代理仍持有上游连接 —— 同一进程内代理与主程序共享网络栈，")
    print("    所以连接表里仍然看得到上游。这是「隔离」而非「隐藏」。")
    print("    若把代理拆成独立进程，主进程那一行才会真正只有 127.0.0.1，")
    print("    但代价是：那个代理进程自己同样会被看到。")
else:
    print("  · 本次采样未捕获到上游连接（采样窗口有限，不代表不存在）")

print("\n" + "=" * 52)
print(f"通过 {ok} / 失败 {fail}")
print("=" * 52)
sys.exit(1 if fail else 0)