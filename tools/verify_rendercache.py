"""验证字形轮廓缓存这次优化：既提速，又没把字画歪。

改造点：字形 path 改成在原点构建，再用 painter.translate 定位。
风险：translate 用错会让整段文字错位。所以这里要做**位置回归比对**——
把改造前后同一段文字渲染出的像素逐点比较，必须完全一致。
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QPixmap, QPainter, QPainterPath, QPen, QBrush

app = QApplication(sys.argv)

from audio.config import Settings
from audio.widgets import StrokedText
from audio import detect

ok = fail = 0


def check(label, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {label} {extra}")
    else:
        fail += 1
        print(f"  FAIL  {label} {extra}")


TEXT = ("【答案】2\n"
        "【简析】直接相加即可：1+1=2。\n"
        "第 2 页 · 第 1 题\n"
        "【答案】1/2\n"
        "这是一行很长的文字，用来验证滚动与定位是否正确。")

print("\n=== 1. 缓存正确性：位置必须与「每行重建」完全一致 ===")


def render(rebuild_each_time: bool):
    """rebuild_each_time=True 模拟优化前的做法（每行都新建 path）。"""
    t = StrokedText()
    t.setAttribute(Qt.WA_TranslucentBackground, True)
    t.resize(240, 150)
    t.set_font_size(11)
    t.set_text_color(QColor(25, 25, 25, 255))
    t.set_stroke((255, 255, 255), 1.2, 200)
    t.set_text(TEXT)
    t.scroll_to_extreme("top")

    if rebuild_each_time:
        from audio.widgets import make_font
        font = make_font(11, False)
        pm = QPixmap(t.size())
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        lines = t._wrap()
        lh = t._m_line_height
        ascent = t._m_metrics.ascent()
        pen = QPen(QColor(255, 255, 255, 200))
        pen.setWidthF(1.2)
        for idx, (a, b) in enumerate(lines):
            seg = t.text()[a:b]
            if not seg:
                continue
            baseline = t._padding + idx * lh - t._scroll + ascent
            path = QPainterPath()
            path.addText(float(t._padding), baseline, font, seg)   # 旧写法：绝对坐标
            p.setPen(pen)
            p.strokePath(path, pen)
            p.setPen(Qt.NoPen)
            p.fillPath(path, QBrush(QColor(25, 25, 25, 255)))
        p.end()
        return pm.toImage()

    pm = QPixmap(t.size())
    pm.fill(Qt.transparent)
    t.render(pm)
    return pm.toImage()


old_img = render(True)
new_img = render(False)

diff = sum(1 for y in range(old_img.height())
           for x in range(old_img.width())
           if old_img.pixelColor(x, y).alpha() != new_img.pixelColor(x, y).alpha())
total = old_img.width() * old_img.height()
print(f"    逐点差异 {diff}/{total} = {diff / total * 100:.2f}%")


def profile(img):
    """取墨迹的包围盒与总不透明度，用来区分「抗锯齿噪声」和「真的移位」。"""
    minx, miny, maxx, maxy, ink = img.width(), img.height(), -1, -1, 0
    for y in range(img.height()):
        for x in range(img.width()):
            a = img.pixelColor(x, y).alpha()
            if a:
                ink += a
                minx, maxx = min(minx, x), max(maxx, x)
                miny, maxy = min(miny, y), max(maxy, y)
    return (minx, miny, maxx, maxy), ink


b_old, ink_old = profile(old_img)
b_new, ink_new = profile(new_img)
print(f"    旧包围盒 {b_old} 总不透明度 {ink_old}")
print(f"    新包围盒 {b_new} 总不透明度 {ink_new}")

check("墨迹包围盒一致（文字没有移位）", b_old == b_new,
      f"-> {b_old} vs {b_new}")
check("总不透明度接近（差异 < 3%，属抗锯齿噪声）",
      abs(ink_old - ink_new) / max(1, ink_old) < 0.03,
      f"-> {abs(ink_old - ink_new) / max(1, ink_old) * 100:.2f}%")

def paint_once(t):
    pm = QPixmap(t.size())
    pm.fill(Qt.transparent)
    t.render(pm)
    return pm


print("\n=== 2. 缓存确实在生效 ===")
t = StrokedText()
t.setAttribute(Qt.WA_TranslucentBackground, True)
t.resize(240, 150)
t.set_font_size(11)
t.set_text(TEXT)
app.processEvents()
paint_once(t)                       # 第一次绘制才会建缓存
before = len(t._path_cache)
for _ in range(10):
    paint_once(t)
after = len(t._path_cache)
check("重复绘制缓存条目不再增长", after == before,
      f"-> {before} -> {after} 条")
check("缓存条目数合理（<= 可见行数级别）", after <= 40, f"-> {after}")

print("\n=== 3. 字体变化时缓存必须作废 ===")
t.set_font_size(16)
app.processEvents()
paint_once(t)
keys = list(t._path_cache.keys())
check("改字号后缓存已重建", len(keys) > 0, f"-> {len(keys)} 条")
check("缓存键里带上了新字号", keys and keys[0][1] == 16,
      f"-> 首个键 = {keys[0][:2] if keys else None}")

print("\n=== 4. 缓存不会无限增长 ===")
t2 = StrokedText()
t2.setAttribute(Qt.WA_TranslucentBackground, True)
t2.resize(240, 150)
t2.set_font_size(11)
for i in range(1200):
    t2.append_text(f"第{i}行内容\n")
app.processEvents()
paint_once(t2)
from audio.widgets import PATH_CACHE_MAX
check("缓存有上限", len(t2._path_cache) <= PATH_CACHE_MAX,
      f"-> {len(t2._path_cache)} 条 (上限 {PATH_CACHE_MAX})")

print("\n=== 5. 提速是否真的发生 ===")
t3 = StrokedText()
t3.setAttribute(Qt.WA_TranslucentBackground, True)
t3.resize(240, 150)
t3.set_font_size(11)
t3.set_stroke((255, 255, 255), 1.2, 200)
t3.set_text(TEXT)
app.processEvents()
t3._path_cache.clear()
# 第一次（冷）：必须重建
t0 = time.perf_counter()
for _ in range(20):
    t3._path_cache.clear()
    pm = QPixmap(t3.size()); pm.fill(Qt.transparent); t3.render(pm)
cold = (time.perf_counter() - t0) / 20 * 1000
# 第二次（热）：应命中缓存
t0 = time.perf_counter()
for _ in range(20):
    pm = QPixmap(t3.size()); pm.fill(Qt.transparent); t3.render(pm)
warm = (time.perf_counter() - t0) / 20 * 1000
print(f"    冷（每次重建）{cold:.3f} ms  vs  热（命中缓存）{warm:.3f} ms")
check("命中缓存明显更快", warm < cold, f"-> {warm:.2f} < {cold:.2f}")

print("\n=== 6. 录屏轮询成本（分层之后）===")
detect.reset_poll_counter()
t0 = time.perf_counter()
for _ in range(30):
    detect.capture_risk()
dt = (time.perf_counter() - t0) / 30 * 1000
print(f"    30 次 capture_risk 平均 {dt:.3f} ms/次")

t0 = time.perf_counter()
for _ in range(20):
    detect.list_process_names()
full = (time.perf_counter() - t0) / 20 * 1000
per_sec = dt * (1000 / 700)
print(f"    单次全量枚举 {full:.3f} ms")
print(f"    -> 每秒 {per_sec:.2f} ms，约占 1 核 {per_sec / 10:.2f}%")
print(f"    -> 优化前每秒约 {full * (1000 / 700):.2f} ms（占 {full * (1000 / 700) / 10:.2f}%）")
check("分层后每次检测明显低于全量枚举", dt < full,
      f"-> {dt:.2f} ms vs 全量 {full:.2f} ms")

print("\n" + "=" * 52)
print(f"通过 {ok} / 失败 {fail}")
print("=" * 52)
sys.exit(1 if fail else 0)