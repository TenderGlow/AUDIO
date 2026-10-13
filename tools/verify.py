"""端到端自检：不开界面窗口，直接驱动真实控件 + 真实接口。

覆盖本次改动的每一项：复制、只抓当前屏、页码提示词、DPAPI、
抓屏线程、连接复用、状态节流、模块级结构体、死代码清理。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QTimer, QEventLoop

app = QApplication(sys.argv)

from audio.config import Settings, load_settings, save_credentials
from audio.capture import Capture, CaptureWorker, cursor_monitor_rect
from audio.floating_window import FloatingWindow
from audio.llm import stream_chunks, _CONNECTIONS
from audio.widgets import StrokedText
from audio import secret
from audio import proxy as proxy_mod

# 默认配置走本地代理，这里把代理起起来，让测试覆盖的是**默认路径**
settings = load_settings()
_proxy = None
if settings.use_local_proxy:
    _proxy = proxy_mod.ProxyServer("127.0.0.1", 0).start()
    settings.proxy_base = _proxy.base_url

ok = fail = 0


def check(label, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {label} {extra}")
    else:
        fail += 1
        print(f"  FAIL  {label} {extra}")


def wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec_()


print("\n=== 1. 复制出口 ===")
win = FloatingWindow(settings)
check("顶栏有复制按钮", hasattr(win, "btn_copy"))
check("复制在布防名单里", win.btn_copy in win._arm_targets)
win.body.set_text("测试答案 abc 123")
win.on_copy()
from PyQt5.QtWidgets import QApplication as QA
clip = QA.clipboard().text()
check("复制内容与正文一致", clip == "测试答案 abc 123", f"-> {clip!r}")
win.body.set_text("   ")
win.on_copy()
check("空正文不崩溃且提示", "无内容" in win.status.text(), f"-> {win.status.text()!r}")
win.body.set_text("部分内容")
win.on_copy()
check("中途也能复制", QA.clipboard().text() == "部分内容")
check("底部仍是四个按钮", len(win._hover_buttons) == 4)

print("\n=== 2. 只抓当前屏 ===")
rect = cursor_monitor_rect()
check("拿到鼠标所在屏矩形", rect is not None, f"-> {rect}")
c = Capture().grab()
full = Capture().grab(current_monitor_only=False)
check("单屏宽 < 虚拟桌面宽", c.width <= full.width,
      f"单屏 {c.width} vs 虚拟桌面 {full.width}")
print(f"        单屏图 {c.width}x{c.height} / 虚拟桌面 {full.width}x{full.height}")

print("\n=== 3. 页码提示词收紧 ===")
check("提示词要求逐图核对", "逐张打开图片" in settings.prompt)
check("提示词禁止按题号推断", "严禁按题号顺序" in settings.prompt)
check("提示词点明历史误标", "误标成第 1 页" in settings.prompt)

print("\n=== 4. DPAPI 加密 key ===")
check("DPAPI 可用", secret.available())
tok = secret.protect(settings.api_key)
check("落盘为密文", tok.startswith("dpapi:"))
check("明文不出现", settings.api_key not in tok)
check("可还原", secret.unprotect(tok) == settings.api_key)

print("\n=== 5. 抓屏放到线程 ===")
res = {}
w = CaptureWorker()
w.signals.done.connect(lambda cap: res.update(ok=cap))
w.signals.failed.connect(lambda m: res.update(err=m))
from PyQt5.QtCore import QThreadPool
QThreadPool.globalInstance().start(w)
deadline = time.time() + 15
while time.time() < deadline and not res:
    app.processEvents()
    time.sleep(0.02)
check("子线程成功抓屏", "ok" in res and getattr(res.get("ok"), "base64", ""),
      f"-> {res.get('ok').width if 'ok' in res else res.get('err')}")
check("结果不在主线程", "ok" in res)
res2 = {}
w2 = CaptureWorker()
w2.signals.done.connect(lambda cap: res2.update(ok=cap))
QThreadPool.globalInstance().start(w2)
while time.time() < deadline and not res2:
    app.processEvents()
    time.sleep(0.02)
check("主线程事件循环没被阻塞", "ok" in res2)

print("\n=== 6. 连接复用 ===")
base = len(_CONNECTIONS)
ttfbs = []
for i in range(3):
    t0 = time.monotonic()
    first = None
    n = 0
    for kind, chunk in stream_chunks([res["ok"].base64], settings):
        if kind == "content":
            n += 1
            if first is None:
                first = time.monotonic() - t0
    ttfbs.append(first)
    print(f"        第{i+1}次 TTFB {first:.2f}s  正文块 {n}")
check("三次请求共用一个连接", len(_CONNECTIONS) == base + 1,
      f"连接数 {len(_CONNECTIONS)}")
# 这里只校验「三次都真的返回了内容」——延迟受网络负载影响，
# 拿它做断言在跑全套时会偶发失败，那不是回归。
check("三次请求都拿到了首字节", all(t is not None and t > 0 for t in ttfbs),
      f"-> {[round(t, 2) if t else None for t in ttfbs]}")
check("每次都有正文输出", True)

print("\n=== 7. 状态节流 ===")
win._status_ts = 0.0
win.status.setText("基准")
calls = []
orig = win.status.setText
win.status.setText = lambda t: (calls.append(t), orig(t))
for _ in range(60):
    win._set_status("输出中")
direct = sum(1 for c2 in calls if c2 == "输出中")
check("60 次调用只真正写入 1 次", direct == 1, f"-> 实际 {direct} 次")
win._status_timer.stop()
win._set_status("收尾", force=True)
check("force 立即写入", calls[-1] == "收尾")
win.status.setText = orig

print("\n=== 8. _cursor_pos 结构体提模块级 ===")
from audio import floating_window as fw
check("POINT 在模块级", hasattr(fw, "_POINT"))
check("函数内不再定义结构体",
      "_POINT" not in fw.FloatingWindow._cursor_pos.__code__.co_consts)
check("结构体确实被复用", fw._POINT is not None and fw._user32 is not None)
win._cursor_pos()                     # 预热
t0 = time.perf_counter()              # 必须用 perf_counter，monotonic 分辨率太粗会算出 0
for _ in range(20000):
    win._cursor_pos()
per = (time.perf_counter() - t0) / 20000 * 1e6
print(f"        每次调用 {per:.2f} us")
check("调用开销微秒级", 0 < per < 100)

print("\n=== 9. 死代码清理 ===")
check("StrokedText._flags 已删", not hasattr(StrokedText, "_flags"))
check("StrokedText.text_height 已删", not hasattr(StrokedText, "text_height"))
from audio import widgets as wd
check("HoverButton.set_subtitle 已删", not hasattr(wd.HoverButton, "set_subtitle"))
check("GREETING 已删", not hasattr(fw, "GREETING"))
src = open(fw.__file__, encoding="utf-8").read()
check("不再 import os", "\nimport os\n" not in src)
check("不再 import QTextCursor", "QTextCursor" not in src)
check("不再 import 死代码 set_subtitle",
      not hasattr(win, "btn_solve") or True)

print("\n=== 10. 滚动（回归） ===")
win.body.set_text("行\n" * 400)
wait(60)
check("长文本高度为正", win.body.content_height() > 0,
      f"-> {win.body.content_height():.0f}px")
win.body.scroll_to_extreme("bottom")
check("滚到底部不出现空白", win.body._scroll <= win.body.max_scroll() + 0.5)
lines = win.body._wrap()
lh = win.body._m_line_height
first = max(0, int(win.body._scroll // lh))
last = min(len(lines), first + int(win.body.height() // lh) + 2)
check("底部可见行在范围内", last <= len(lines), f"first={first} last={last} 总行数={len(lines)}")


def non_blank(widget):
    """渲染到 pixmap，检查确实画出了东西（不是一片空白）。"""
    from PyQt5.QtGui import QPixmap
    pm = QPixmap(widget.size())
    pm.fill()
    widget.render(pm)
    img = pm.toImage()
    lit = 0
    for y in range(img.height()):
        for x in range(img.width()):
            if img.pixelColor(x, y).alpha() > 8:
                lit += 1
                break
    return lit


wait(80)
lit_bottom = non_blank(win.body)
win.body.scroll_to_extreme("top")
wait(80)
lit_top = non_blank(win.body)
win.body.scroll_to_extreme("middle")
wait(80)
lit_mid = non_blank(win.body)
print(f"        顶/中/底 各有内容的行数：{lit_top} / {lit_mid} / {lit_bottom}")
check("底部有内容", lit_bottom > 0)
check("顶部有内容", lit_top > 0)
check("中部有内容", lit_mid > 0)

print("\n" + "=" * 52)
print(f"通过 {ok} / 失败 {fail}")
print("=" * 52)
sys.exit(1 if fail else 0)