"""验证：主程序被强杀后，代理进程会不会留下孤儿。

这是实测中发现的一个真 bug：早期版本强杀主程序后，代理还在跑、还在连上游。
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ok = fail = 0


def check(label, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {label} {extra}")
    else:
        fail += 1
        print(f"  FAIL  {label} {extra}")


def running_procs():
    """返回 [(pid, cmdline)]，只取 python 且涉及本项目的。"""
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" "
         "| Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"],
        capture_output=True, text=True, errors="replace").stdout.strip()
    import json
    rows = []
    if not out:
        return rows
    data = json.loads(out)
    if isinstance(data, dict):
        data = [data]
    for d in data:
        cmd = (d.get("CommandLine") or "")
        if "main.py" in cmd or "audio.proxy" in cmd:
            rows.append((d["ProcessId"], cmd))
    return rows


def count_proxies():
    return list_proxies()


def count_main():
    return list_mains()


print("=== 清理环境 ===")
from harness import cleanup_all, list_mains, list_proxies, wait_for
cleanup_all()
print(f"  起始：主程序 {len(list_mains())} 个，代理 {len(list_proxies())} 个")

print("\n=== A. 正常启动 main.py（会拉起代理进程）===")
proc = subprocess.Popen([sys.executable, "main.py"], cwd=ROOT,
                        creationflags=subprocess.CREATE_NO_WINDOW)
wait_for(lambda: bool(list_mains()) and bool(list_proxies()), timeout=45)

mains = count_main()
proxies = count_proxies()
check("主程序起来了", len(mains) >= 1, f"-> {mains}")
check("代理进程起来了", len(proxies) >= 1, f"-> {proxies}")

print("\n=== B. 强杀主程序（模拟任务管理器 End task）===")
# 必须杀**自己刚起的那个**，不能只杀 mains[0]——环境里若有残留就选错对象了
target = proc.pid
print(f"        强杀 pid={target}")
subprocess.run(["taskkill", "/F", "/PID", str(target)], capture_output=True)
# Job Object 应该立刻把代理一起带走；父进程监视是最多 1 秒的兜底
time.sleep(4)
left_proxies = list_proxies()
left_mains = list_mains()
print(f"  强杀后：主程序 {len(left_mains)} 个，代理 {len(left_proxies)} 个")
check("强杀主程序后代理被连带清理", len(left_proxies) == 0,
      "" if not left_proxies else f"-> 残留 {left_proxies}")
check("主程序已终止", len(left_mains) == 0)

print("\n=== C. shutdown.py 也能清残留代理 ===")
if left_proxies:
    subprocess.run([sys.executable, "audio/shutdown.py", "stop"],
                   cwd=ROOT, capture_output=True)
    time.sleep(2)
    check("stop() 清掉了残留代理", len(list_proxies()) == 0)
else:
    check("无需清理（Job 已处理）", True)

# 收尾
cleanup_all(verbose=False)

print("\n" + "=" * 52)
print(f"通过 {ok} / 失败 {fail}")
print("=" * 52)
sys.exit(1 if fail else 0)