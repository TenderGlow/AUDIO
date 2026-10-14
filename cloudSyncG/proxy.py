"""本地回环转发代理：主程序只和 127.0.0.1 说话，上游地址只存在于本进程内。

**为什么做这个**
主程序直接连 `api.deepseek.com` 时，任何按进程查看连接的工具（资源监视器、
netstat、EDR、简单杀软）都会看到主程序持有一条到公网 443 的外联。
加了这一层之后，主程序那一行只有 `127.0.0.1:<本地端口>`，不直接暴露上游。

**边界（说在前面，别被误解）**
这只是"让主程序那条连接不直接指向上游"，**并没有、也无法让连接从系统层面消失**：
本进程自己照样持有一条到 api.deepseek.com:443 的连接，在进程级连接列表里依然可见。
想让系统层面查不到，只有内核 hooking / 驱动过滤，那属于规避系统审计，不做。

**实现要点**
- 纯标准库，`ThreadingHTTPServer` + `http.client`，不引入第三方依赖。
- SSE 流式：`stream=True` 时**边收边发**，逐块 flush，绝不缓冲整个响应，
  否则流式输出会退化成"等全部生成完才一次性显示"。
- 认证头由代理注入：主程序只发一个本地令牌，**API Key 不经过主程序**，
  只存在于代理进程内存里。
"""
from __future__ import annotations

import http.client
import json
import os
import socket
import ssl
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

# 上游：只在代理进程内出现。
# 换后端时这里要跟着改三处：host / UPSTREAM_PATH_PREFIX / 转发用的完整路径。
# 之前把转发路径写死成 "/chat/completions"，换到带 /compatible-mode/v1 前缀的
# OpenAI 兼容端点就会 404——所以路径必须由前缀拼出来，不能写死。
UPSTREAM_HOST = "llm-69ai4267tbwdiss6.cn-beijing.maas.aliyuncs.com"
UPSTREAM_PORT = 443
UPSTREAM_PATH_PREFIX = "/compatible-mode/v1"

# 本地令牌：主程序用它换取代理转发服务。
# 不校验等于任何本机进程都能借用这个代理，校验是为了避免代理变成开放的 API 中转站。
LOCAL_TOKEN = "cloudSyncG-local-proxy"

UPSTREAM_PATH = f"{UPSTREAM_PATH_PREFIX}/chat/completions"

# 本地契约：主程序只发短路径，跟后端换不换无关。
# 真实上游路径由代理内部映射——否则主程序要跟着换后端改代码。
LOCAL_PATHS = ("/chat/completions",)
# 兼容：也接受主程序直接带上游前缀的写法
UPSTREAM_PATHS = LOCAL_PATHS + (UPSTREAM_PATH,)


def upstream_base_url() -> str:
    """仅供诊断用；主程序不应该调用它。"""
    return f"https://{UPSTREAM_HOST}{UPSTREAM_PATH_PREFIX}"


class ProxyHandler(BaseHTTPRequestHandler):
    """把 /chat/completions 原样转发到上游，只做三件事：
    1) 校验本地令牌 2) 注入真实的 Authorization 头 3) 流式转发 SSE。
    """

    protocol_version = "HTTP/1.1"
    server_version = "cloudSyncGProxy/1.0"

    def log_message(self, fmt, *args):  # noqa: A002
        """不往控制台打日志，避免和主程序的输出混在一起。"""

    def _reject(self, code: int, message: str) -> None:
        body = json.dumps({"error": {"message": message}}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        return self.headers.get("X-cloudSyncG-Token", "") == LOCAL_TOKEN

    def do_GET(self):  # noqa: N802
        """健康检查，方便主程序确认代理活着。"""
        if self.path == "/_ping":
            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._reject(404, "not found")

    def do_POST(self):  # noqa: N802
        path = urlparse(self.path).path
        if path not in UPSTREAM_PATHS:
            self._reject(404, f"unsupported path: {path}")
            return
        if not self._authorized():
            self._reject(401, "bad local token")
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._reject(400, "bad Content-Length")
            return
        payload = self.rfile.read(length)

        try:
            streaming = _is_stream_request(payload)
        except Exception:  # noqa: BLE001
            streaming = False

        try:
            if streaming:
                self._forward_stream(payload)
            else:
                self._forward_buffered(payload)
        except (ConnectionError, socket.timeout, OSError) as exc:
            # 客户端可能已经断了（用户取消/关窗），这种情况不必再报错
            try:
                self._reject(502, f"upstream error: {exc}")
            except Exception:  # noqa: BLE001
                pass
        except Exception as exc:  # noqa: BLE001
            try:
                self._reject(500, str(exc))
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    # 非流式：收完再发
    # ------------------------------------------------------------------
    def _forward_buffered(self, payload: bytes) -> None:
        conn = _upstream()
        try:
            conn.request("POST", UPSTREAM_PATH, body=payload, headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key()}",
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            })
            resp = conn.getresponse()
            body = resp.read()
            self.send_response(resp.status)
            self.send_header("Content-Type",
                             resp.getheader("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    # 流式：必须边收边发（chunked），否则流式输出会失去意义
    # ------------------------------------------------------------------
    def _forward_stream(self, payload: bytes) -> None:
        conn = _upstream()
        try:
            conn.request("POST", UPSTREAM_PATH, body=payload, headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key()}",
                "Accept": "text/event-stream",
                "Accept-Encoding": "identity",
            })
            resp = conn.getresponse()
            if resp.status != 200:
                detail = resp.read()[:300]
                self._reject(resp.status, detail.decode("utf-8", "ignore"))
                return

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            # chunked：长度未知，必须自己分块
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()

            while True:
                chunk = resp.read1(4096) if hasattr(resp, "read1") else resp.read(1)
                if not chunk:
                    break
                self.wfile.write(b"%X\r\n" % len(chunk) + chunk + b"\r\n")
                self.wfile.flush()        # 每块都刷，客户端才能实时看到
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def _is_stream_request(payload: bytes) -> bool:
    try:
        return bool(json.loads(payload.decode("utf-8")).get("stream"))
    except Exception:  # noqa: BLE001
        return False


def _upstream() -> http.client.HTTPSConnection:
    return http.client.HTTPSConnection(
        UPSTREAM_HOST, UPSTREAM_PORT, timeout=120,
        context=ssl.create_default_context())


def api_key() -> str:
    """代理进程自己去读凭据，主程序不碰 Key。"""
    from .config import load_settings
    return load_settings().api_key


class ProxyServer:
    """把代理包成可启停的对象。"""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self._server = ThreadingHTTPServer((host, port), ProxyHandler)
        self._server.daemon_threads = True
        self.host = self._server.server_address[0]
        self.port = self._server.server_address[1]
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> "ProxyServer":
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        name="cloudSyncG-proxy", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        try:
            self._server.shutdown()
            self._server.server_close()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# 孤儿清理：主程序被杀时，代理必须跟着死
# ---------------------------------------------------------------------------
def _make_job_object():
    """建一个 Job Object 并设置 KILL_ON_JOB_CLOSE。

    效果：主程序进程一退出（哪怕是被 Taskkill 强杀），内核会连带杀掉 Job 里的代理。
    没有这一层的话，强杀主程序会留下一个还在跑、还在连上游的孤儿代理。
    """
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in
                        ("ReadOperationCount", "WriteOperationCount",
                         "OtherOperationCount", "ReadTransferCount",
                         "WriteTransferCount", "OtherTransferCount")]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD)]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
        if not kernel32.SetInformationJobObject(
                job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
                ctypes.byref(info), ctypes.sizeof(info)):
            kernel32.CloseHandle(job)
            return None
        return job
    except Exception:  # noqa: BLE001
        return None


def _assign_to_job(job, pid: int) -> bool:
    """把子进程塞进 Job Object。"""
    if os.name != "nt" or not job:
        return False
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        PROCESS_SET_QUOTA = 0x0100
        PROCESS_TERMINATE = 0x0001
        handle = kernel32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE,
                                      False, pid)
        if not handle:
            return False
        try:
            return bool(kernel32.AssignProcessToJobObject(job, handle))
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001
        return False


def _close_handle(h) -> None:
    if os.name != "nt" or not h:
        return
    try:
        import ctypes
        ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(h)
    except Exception:  # noqa: BLE001
        pass


def _parent_alive(pid: int) -> bool:
    """父进程还在不在。"""
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        SYNCHRONIZE = 0x00100000
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if not handle:
            return False
        try:
            WAIT_TIMEOUT = 0x00000102
            return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001
        return True


def spawn_detached(host: str = "127.0.0.1", port: int = 8781):
    """把代理拉起成**独立进程**。

    为什么必须独立进程：进程的网络栈是隔离的。代理如果跑在主程序进程内，
    两者共用同一份连接表——实测主程序照样能看到到上游的那条连接，代理等于没起作用。
    拆成独立进程后，主程序的连接列表里才真的只剩 `127.0.0.1`。

    代价说清楚：那个代理进程自己会有一条到上游的连接。
    **总量没有减少，只是从主程序挪到了代理进程。**

    另外会把子进程绑进 Job Object 并传入父进程 pid，保证主程序被强杀时代理一起死。
    """
    import subprocess
    import sys
    from . import runtime

    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    job = _make_job_object()

    # 冻成 exe 之后没有 `__main__` 模块可跑，只能让同一个二进制自己
    # 按 `--proxy` 参数进入代理模式；进程名因此仍然是 CloudSync.exe。
    if runtime.is_frozen():
        argv = [sys.executable, "--proxy",
                "--host", str(host), "--port", str(port),
                "--parent-pid", str(os.getpid())]
        cwd = runtime.app_dir()
    else:
        argv = [sys.executable, "-u", "-m", "cloudSyncG.proxy",
                "--host", str(host), "--port", str(port),
                "--parent-pid", str(os.getpid())]
        cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    runtime.prime_child_env()
    try:
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            creationflags=flags,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:  # noqa: BLE001
        _close_handle(job)
        return None
    # Job 句柄必须在主程序整个生命周期内保持打开。
    # 挂在 Popen 上防止被 GC 回收——一关就触发 KILL_ON_JOB_CLOSE，代理会被立刻杀掉。
    proc._cloudSyncG_job = job      # noqa: SLF001
    if job is not None:
        _assign_to_job(job, proc.pid)
    return proc


def wait_until_ready(base_url: str, timeout: float = 8.0) -> bool:
    """轮询健康检查，确认代理起来了。"""
    import urllib.request
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(base_url.rstrip("/") + "/_ping",
                                       timeout=1) as resp:
                if json.loads(resp.read()).get("ok"):
                    return True
        except Exception:  # noqa: BLE001
            time.sleep(0.15)
    return False


def main(argv: "list[str] | None" = None) -> int:
    """代理进程的入口。

    源码模式是 `python -m cloudSyncG.proxy ...`，打包后是
    `CloudSync.exe --proxy ...`，两者在这里汇合。
    """
    import argparse

    parser = argparse.ArgumentParser(description="cloudSyncG 本地回环代理")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8781)
    parser.add_argument("--parent-pid", type=int, default=0,
                        help="父进程 pid，父进程没了就自行退出")
    args = parser.parse_args(sys.argv[1:] if argv is None else list(argv))

    server = ProxyServer(args.host, args.port)
    print(f"本地代理已启动：{server.base_url}  ->  上游 https://{UPSTREAM_HOST}",
          flush=True)
    server.start()

    # 兜底：即使 Job Object 没生效，也定期检查父进程，父进程没了就退出。
    # 孤儿代理会继续连着上游，留着既浪费连接也说不清楚。
    try:
        while True:
            time.sleep(1.0)
            if args.parent_pid and not _parent_alive(args.parent_pid):
                server.stop()
                break
    except KeyboardInterrupt:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
