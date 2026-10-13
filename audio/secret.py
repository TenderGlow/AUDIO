"""把 API Key 用 Windows DPAPI 加密后再落盘。

DPAPI（CryptProtectData）用**当前 Windows 账户**的登录凭据做加密：
只有同一账户、同一台机器上的进程能解出来，换个账户或拷到别的机器就是一堆废字节。
比明文存盘安全，也不需要引入 pycryptodome 之类的额外依赖。

工具函数全部失败时安全降级：加密不可用就退回明文，解密失败返回 None，调用方走默认值。
"""
from __future__ import annotations

import base64
import ctypes
import os
from ctypes import wintypes

PREFIX = "dpapi:"          # 密文前缀，用来区分它是密文还是历史遗留的明文
_ENTROPY = b"Audio/api-key/v1"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1     # 不弹任何 UI
_CRYPT_VERIFYCONTEXT = 0xF0000000

if os.name == "nt":
    _CRYPT32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
else:  # pragma: no cover - 仅本机 Windows 使用
    _CRYPT32 = None
    _KERNEL32 = None


class _DATA_BLOB(ctypes.Structure):
    """CRYPT_DATA_BLOB：cbData 长度 + pbData 指针。"""
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _to_blob(data: bytes) -> tuple[_DATA_BLOB, ctypes.Array]:
    """构造 DATA_BLOB。返回的 buffer 必须被调用方持有住。

    注意：ctypes 的 POINTER 字段**不会**给原 buffer 续命，
    如果 buffer 被 GC 回收，指针就变成野指针（表现为随机崩溃或 DPAPI 直接失败）。
    """
    buf = ctypes.create_string_buffer(data, len(data))
    blob = _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    return blob, buf


def _local_free(ptr) -> None:
    """LocalFree 在 kernel32 里，不在 crypt32 里。"""
    if ptr:
        _KERNEL32.LocalFree(ptr)


def available() -> bool:
    return _CRYPT32 is not None and _KERNEL32 is not None


def protect(plain_text: str) -> str:
    """加密成 `dpapi:<base64>`；不可用时原样返回（调用方会退回明文存储）。"""
    if not available() or not plain_text:
        return plain_text
    try:
        src, _src_buf = _to_blob(plain_text.encode("utf-8"))
        entropy, _ent_buf = _to_blob(_ENTROPY)
        out = _DATA_BLOB()
        ok = _CRYPT32.CryptProtectData(
            ctypes.byref(src), "Audio", ctypes.byref(entropy),
            None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out))
        if not ok:
            return plain_text
        try:
            raw = ctypes.string_at(out.pbData, out.cbData)
            return PREFIX + base64.b64encode(raw).decode("ascii")
        finally:
            _local_free(out.pbData)
    except Exception:  # noqa: BLE001 - 任何异常都退回明文，绝不因为加密把 key 弄丢
        return plain_text


def unprotect(token: str) -> str | None:
    """解密 `dpapi:` 密文；不是密文格式或解密失败都返回 None。"""
    if not token.startswith(PREFIX):
        return None            # 历史明文，交给调用方当作"未加密"
    if not available():
        return None
    try:
        raw = base64.b64decode(token[len(PREFIX):])
        src, _src_buf = _to_blob(raw)
        entropy, _ent_buf = _to_blob(_ENTROPY)
        out = _DATA_BLOB()
        ok = _CRYPT32.CryptUnprotectData(
            ctypes.byref(src), None, ctypes.byref(entropy),
            None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out))
        if not ok:
            return None
        try:
            return ctypes.string_at(out.pbData, out.cbData).decode("utf-8")
        finally:
            _local_free(out.pbData)
    except Exception:  # noqa: BLE001
        return None


__all__ = ["protect", "unprotect", "available", "PREFIX"]