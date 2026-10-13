"""后端 LM 调用：DeepSeek 流式接口（多模态截图 + 思考过程分离）。

**两条链路，按 `use_local_proxy` 开关切换：**

- 默认（直连）：`http.client` 直连 `api.deepseek.com`，并缓存连接复用长连接，
  省掉每次请求的 TCP + TLS 握手（实测 100~300ms 首字节延迟）。
- 走本地代理（`use_local_proxy = True`）：主程序只连 `127.0.0.1`，
  上游地址和 API Key 都留在代理进程里，主程序这边只有一条回环连接。

> 注意：走代理只是让**主程序那条连接**不直接指向上游。上游连接在代理进程上依然存在，
> 进程级连接列表里仍然可见。这不是"隐藏"，是"隔离"。
"""
from __future__ import annotations

import http.client
import json
import ssl
import threading
from urllib.parse import urlparse

from PyQt5.QtCore import QObject, QRunnable, pyqtSignal

from .config import Settings

_CONNECTION_LOCK = threading.Lock()
_CONNECTIONS: dict[str, http.client.HTTPSConnection] = {}

# 本地代理模式下的认证头：主程序只带这个本地令牌，真 Key 由代理注入
_PROXY_TOKEN_HEADER = "X-cloudSyncG-Token"


def _connect(url: str, timeout: int) -> http.client.HTTPSConnection:
    """拿到可复用的长连接；失效或出错时自动重建。"""
    parts = urlparse(url)
    host = parts.hostname
    port = parts.port or (443 if parts.scheme == "https" else 80)
    scheme = parts.scheme
    key = f"{scheme}://{host}:{port}"
    with _CONNECTION_LOCK:
        conn = _CONNECTIONS.get(key)
        if conn is None:
            if scheme == "https":
                conn = http.client.HTTPSConnection(
                    host, port, timeout=timeout,
                    context=ssl.create_default_context())
            else:
                conn = http.client.HTTPConnection(host, port, timeout=timeout)
            _CONNECTIONS[key] = conn
        return conn


def _drop(url: str) -> None:
    """连接出错后丢弃，下次重建。"""
    parts = urlparse(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    key = f"{parts.scheme}://{parts.hostname}:{port}"
    with _CONNECTION_LOCK:
        conn = _CONNECTIONS.pop(key, None)
    if conn is not None:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def build_payload(images, settings: Settings) -> dict:
    """images 为一或多张截图的 base64 列表（按顺序对应第 1、2、3、4 页）。"""
    if isinstance(images, str):
        images = [images]
    content: list[dict] = [{"type": "text", "text": settings.prompt}]
    for index, image in enumerate(images, 1):
        # 单图也必须写「第 1 页截图：」：提示词里要求模型逐图核对这个标签，
        # 如果这里改成别的说法（如"整屏截图"），模型会找不到标签而以为没收到图。
        content.append({"type": "text", "text": f"第 {index} 页截图："})
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image}"}})
    return {
        "model": settings.model,
        "stream": bool(settings.stream),
        # 思考档位：high；服务端若不认这个字段会自行忽略
        "reasoning_effort": settings.reasoning_effort,
        "messages": [
            {"role": "system",
             "content": "你是一个会分题号、给出答案与简要解析的解题助手。"},
            {"role": "user", "content": content},
        ],
        "temperature": 0.2,
    }


def _parse_sse(response):
    """从 SSE 字节流里逐行解析出 (reasoning|content, 文本)。"""
    while True:
        raw = response.readline()
        if not raw:
            break
        line = raw.decode("utf-8", "ignore").strip()
        if not line or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            continue
        try:
            delta = chunk["choices"][0]["delta"]
        except (KeyError, IndexError, TypeError):
            continue
        reasoning = delta.get("reasoning_content")
        if reasoning:
            yield "reasoning", reasoning
        content = delta.get("content")
        if content:
            yield "content", content


def _close_response(response):
    # 关键：必须把响应体读完，否则残留字节会被下一次请求当成响应开头解析，
    # 表现为第二次请求 BadStatusLine —— 复用长连接时这个坑很容易踩。
    try:
        response.read()
    except Exception:  # noqa: BLE001
        pass
    try:
        response.close()
    except Exception:  # noqa: BLE001
        pass


def stream_chunks(images, settings: Settings):
    """流式求解，逐块 yield ("reasoning"|"content", 增量文本)。"""
    if isinstance(images, str):
        images = [images]

    if settings.use_local_proxy:
        yield from _stream_via_proxy(images, settings)
        return

    url = settings.api_base.rstrip("/") + "/chat/completions"
    body = json.dumps(build_payload(images, settings)).encode("utf-8")
    headers = {"Content-Type": "application/json",
               "Authorization": f"Bearer {settings.api_key}",
               "Accept": "text/event-stream"}

    try:
        conn = _connect(url, settings.timeout_s)
        conn.request("POST", urlparse(url).path or "/chat/completions",
                     body=body, headers=headers)
        response = conn.getresponse()
        if response.status != 200:
            detail = response.read().decode("utf-8", "ignore")[:300]
            _drop(url)
            raise RuntimeError(f"HTTP {response.status}：{detail}")
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001 - 连接层异常统一处理
        _drop(url)
        raise RuntimeError(f"网络请求失败：{exc}") from exc

    try:
        yield from _parse_sse(response)
    except Exception:
        _drop(url)      # 流中断 → 丢弃连接，下次重建
        raise
    finally:
        _close_response(response)


def _stream_via_proxy(images, settings: Settings):
    """经本地代理请求：主程序只和 127.0.0.1 通信。

    代理回的是 chunked 响应（长度未知），`http.client` 会自己解包，
    所以这里的读法和直连完全一样——这也是为什么代理必须正确发 chunk 结束标记。
    """
    body = json.dumps(build_payload(images, settings)).encode("utf-8")
    url = settings.proxy_base.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json",
               "Accept": "text/event-stream",
               _PROXY_TOKEN_HEADER: settings.proxy_token}

    try:
        conn = _connect(url, settings.timeout_s)
        conn.request("POST", urlparse(url).path or "/chat/completions",
                     body=body, headers=headers)
        response = conn.getresponse()
        if response.status != 200:
            detail = response.read().decode("utf-8", "ignore")[:300]
            _drop(url)
            raise RuntimeError(f"代理返回 HTTP {response.status}：{detail}")
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001
        _drop(url)
        raise RuntimeError(f"本地代理连接失败：{exc}") from exc

    try:
        yield from _parse_sse(response)
    except Exception:
        _drop(url)
        raise
    finally:
        _close_response(response)


class WorkerSignals(QObject):
    reasoning = pyqtSignal(str)      # 思考过程增量
    content = pyqtSignal(str)        # 正文增量
    finished = pyqtSignal(str, str)  # 完整正文, 完整思考
    failed = pyqtSignal(str)


class SolveWorker(QRunnable):
    """在线程池里跑流式请求，逐块把结果送回界面。"""

    def __init__(self, images, settings: Settings):
        super().__init__()
        self._images = images
        self._settings = settings
        self.signals = WorkerSignals()

    def run(self) -> None:
        reasoning_parts: list[str] = []
        content_parts: list[str] = []
        try:
            for kind, text in stream_chunks(self._images, self._settings):
                if kind == "reasoning":
                    reasoning_parts.append(text)
                    self.signals.reasoning.emit(text)
                else:
                    content_parts.append(text)
                    self.signals.content.emit(text)
        except Exception as exc:  # noqa: BLE001
            self.signals.failed.emit(str(exc))
            return
        self.signals.finished.emit("".join(content_parts),
                                   "".join(reasoning_parts))