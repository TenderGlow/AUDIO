"""多页真实请求：造4 页内容互不相同的题目图，一次性发送，核对页码。

注意：不能用 QPainter 画图——offscreen 平台下 Qt 不渲染文字，画出来是全白图，
模型会如实回答"没有内容"。这里改用 PIL 直接画字。
"""
import base64
import io
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import QApplication

app = QApplication(sys.argv)

from PIL import Image, ImageDraw, ImageFont

from cloudSyncG.config import load_settings
from cloudSyncG.llm import stream_chunks, build_payload
from cloudSyncG import proxy as proxy_mod
import json

settings = load_settings()
_proxy = None
if settings.use_local_proxy:
    _proxy = proxy_mod.ProxyServer("127.0.0.1", 0).start()
    settings.proxy_base = _proxy.base_url

QUESTIONS = [
    "1 + 1 = ?",
    "sin(30 degrees) = ?",
    "a square side grows 10 percent, area grows how many percent?",
    "binary 1011 equals what in decimal?",
]


def font(size):
    for name in ("msyh.ttc", "msyhbd.ttc", "simhei.ttf", "arial.ttf"):
        path = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", name)
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def make_page(n):
    img = Image.new("RGB", (1200, 700), "white")
    d = ImageDraw.Draw(img)
    d.text((40, 40), f"Page {n} screenshot", font=font(44), fill="black")
    d.text((40, 180), f"Question {n}: {QUESTIONS[n - 1]}", font=font(30), fill="black")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


images = []
for n in range(1, 5):
    raw = make_page(n)
    img = Image.open(io.BytesIO(raw))
    dark = sum(1 for y in range(0, 700, 4) for x in range(0, 1200, 4)
               if sum(img.getpixel((x, y))) < 400)
    print(f"page{n}: {len(raw)} bytes, 深色采样点 {dark}")
    images.append(base64.b64encode(raw).decode())

body = json.dumps(build_payload(images, settings))
print(f"\n请求体 {len(body) / 1024:.0f} KB / {len(images)} 张图")

t0 = time.monotonic()
parts, first, reason_len = [], None, 0
for kind, chunk in stream_chunks(images, settings):
    if kind == "content":
        if first is None:
            first = time.monotonic() - t0
        parts.append(chunk)
    else:
        reason_len += len(chunk)

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
text = "".join(parts)
print(f"首字节 {first:.2f}s | 总耗时 {time.monotonic() - t0:.2f}s "
      f"| 正文 {len(text)} 字 | 思维链 {reason_len} 字（不显示）")
print("\n" + "=" * 60)
print(text)
print("=" * 60)

print("\n--- 逐页核对（模型用中文作答，按答案关键字核对）---")
# 每页的正确答案（模型可能写成 1/2 或 \frac{1}{2}，两种都算对）
KEYS = [["2"], ["1/2", "\\frac{1}{2}", "0.5"], ["21"], ["11"]]
allgood = True
for n, alts in enumerate(KEYS, 1):
    hit = any(a in text for a in alts)
    allgood &= hit
    print(f"page{n} 应含答案 {alts}: {'命中' if hit else '缺失'}")
print(f"\n四页是否都被解答: {'是' if allgood else '否'}")

print("\n--- 页码标签核对（每页都应恰好出现一次）---")
pageok = True
for n in range(1, 5):
    cnt = text.count(f"第 {n} 页")
    pageok &= cnt == 1
    print(f"第 {n} 页出现 {cnt} 次 {'OK' if cnt == 1 else '异常'}")
print(f"页码标注是否正确: {'是' if pageok else '否'}")

sys.exit(0 if (allgood and pageok) else 1)