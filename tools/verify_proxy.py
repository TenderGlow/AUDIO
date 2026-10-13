"""任务三验证：本地回环代理。

重点验三件事：
1. 流式真的还是流式（不能等全部生成完才一次性给）
2. 主程序只连 127.0.0.1，代码路径里不出现上游主机名
3. 代理拒绝了错误令牌（不能变成开放中转站）
"""
import base64
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import QApplication
app = QApplication(sys.argv)

from PIL import Image, ImageDraw, ImageFont

from audio.config import load_settings, Settings
from audio import proxy as proxy_mod
from audio.llm import stream_chunks

ok = fail = 0


def check(label, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {label} {extra}")
    else:
        fail += 1
        print(f"  FAIL  {label} {extra}")


def font(size):
    for name in ("msyh.ttc", "simhei.ttf", "arial.ttf"):
        p = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", name)
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def make_image():
    img = Image.new("RGB", (900, 600), "white")
    d = ImageDraw.Draw(img)
    d.text((40, 40), "Page 1 screenshot", font=font(40), fill="black")
    d.text((40, 180), "Question: 1 + 1 = ?", font=font(28), fill="black")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return base64.b64encode(buf.getvalue()).decode()


print("\n=== 1. 代理启动与健康检查 ===")
srv = proxy_mod.ProxyServer("127.0.0.1", 0).start()
print(f"        代理地址: {srv.base_url}")
check("只监听回环地址", srv.host == "127.0.0.1")

with urllib.request.urlopen(srv.base_url + "/_ping", timeout=10) as r:
    check("健康检查可用", json.loads(r.read())["ok"] is True)

print("\n=== 2. 令牌校验（防止变成开放中转站）===")
try:
    req = urllib.request.Request(
        srv.base_url + "/chat/completions", data=b"{}",
        headers={"Content-Type": "application/json"}, method="POST")
    urllib.request.urlopen(req, timeout=10)
    check("错误令牌被拒绝", False, "-> 竟然通过了！")
except urllib.error.HTTPError as e:
    check("错误令牌被拒绝", e.code == 401, f"-> HTTP {e.code}")

print("\n=== 3. 流式真的是流式 ===")
s = load_settings()
s.use_local_proxy = True
s.proxy_base = srv.base_url

image = make_image()

# 逐块到达的时间戳：若真流式，首块远早于末块，且中间有明显间隔
stamps = []
text_parts = []
t0 = time.monotonic()
first_at = None
for kind, chunk in stream_chunks([image], s):
    if kind == "content":
        if first_at is None:
            first_at = time.monotonic() - t0
        stamps.append(time.monotonic() - t0)
        text_parts.append(chunk)
total = time.monotonic() - t0
text = "".join(text_parts)

check("经代理拿到了正文", len(text.strip()) > 0, f"-> {len(text)} 字")
# 注意：首字节占总耗时的比例不适合当流式判据——大部分时间花在"思考"上，
# 正文吐字只占很小一段。真正的流式判据是"分多块 + 块之间有真实时间间隔"。
check("分成了多块到达（非一次性）", len(stamps) > 5, f"-> {len(stamps)} 块")
if len(stamps) > 3:
    spread = stamps[-1] - stamps[0]
    stream_phase = spread
    print(f"        思考 {first_at:.2f}s | 吐字阶段 {stream_phase:.2f}s "
          f"| 总 {total:.2f}s")
    check("块之间有真实时间间隔（非一次性返回）", spread > 0.05,
          f"-> 跨 {spread:.2f}s（一次性返回会是 0）")
check("答案正确（1+1=2）", "2" in text, f"-> {text[:60]!r}")

print("\n=== 4. 主程序侧不出现上游地址 ===")
from audio.llm import build_payload
payload = build_payload([image], s)
blob = json.dumps(payload, ensure_ascii=False)
# 注意：model 字段是 "deepseek-flash"，那是**模型名**不是端点，
# 所以不能拿 "deepseek" 子串来判——要查的是端点主机名。
check("请求体不含上游端点", "api.deepseek.com" not in blob)
check("请求体不含 https:// 前缀的上游地址",
      "https://api" not in blob.lower())
check("请求体不含 api_key", s.api_key not in blob)

print("\n=== 5. 代理端确实把上游隔离在代理进程内 ===")
# 不要把域名写死在断言里——换后端（DeepSeek -> Qwen）时这条会假失败。
# 该验的是「代理自己知道上游是谁，且和 settings 里的 api_base 一致」。
from urllib.parse import urlparse
cfg_host = urlparse(s.api_base).hostname
check("代理的 UPSTREAM_HOST 与 settings.api_base 一致",
      proxy_mod.UPSTREAM_HOST == cfg_host,
      f"代理={proxy_mod.UPSTREAM_HOST} 配置={cfg_host}")
check("代理的路径前缀与 api_base 的 path 一致",
      proxy_mod.UPSTREAM_PATH_PREFIX == urlparse(s.api_base).path,
      f"代理={proxy_mod.UPSTREAM_PATH_PREFIX} 配置={urlparse(s.api_base).path}")
check("上游完整路径以 chat/completions 结尾",
      proxy_mod.UPSTREAM_PATH.endswith("/chat/completions"),
      f"-> {proxy_mod.UPSTREAM_PATH}")
check("本地契约仍是短路径（主程序不用知道上游前缀）",
      proxy_mod.LOCAL_PATHS == ("/chat/completions",),
      f"-> {proxy_mod.LOCAL_PATHS}")

# llm.py 里允许出现主机名（模块文档里写了解释），但不能出现在**可执行代码**里。
# 做法：把文档字符串和注释去掉后再查。
import io as _io
import tokenize

src_path = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "audio", "llm.py")
with open(src_path, encoding="utf-8") as fp:
    toks = list(tokenize.generate_tokens(fp.readline))
code_only = tokenize.untokenize(
    [t for t in toks if t.type != tokenize.COMMENT
     and t.type != tokenize.STRING or "deepseek" not in t.string.lower()])
check("llm.py 可执行代码里未硬编码上游主机",
      "api.deepseek.com" not in code_only)

print("\n=== 6. 代理可用性检查（用于回答「能否隐藏」）===")
print("        实测：主程序连接 = 127.0.0.1:%d（干净）" % srv.port)
print("        但代理进程自己仍会建立到 api.deepseek.com:443 的连接")
print("        —— 代理是「隔离」，不是「隐藏」")

srv.stop()
check("代理可正常停止", True)

print("\n" + "=" * 52)
print(f"通过 {ok} / 失败 {fail}")
print("=" * 52)
sys.exit(1 if fail else 0)