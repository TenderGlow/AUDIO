"""自绘控件：圆角面板 / 正文 / 悬停按钮 / 小圆圈 / 滚动箭头。

**性能要点（踩过坑才写下来的）**

正文 `StrokedText` 的换行采用**增量行表**：`_wrap()` 只处理新追加的字符，
并且"测量"（算滚动范围）与"绘制"（paintEvent）**共用同一张行表**。
这一条同时解决了两个问题：

1. 早期版本每次追加都从头重测，是 O(n²)：7500 次追加要 85 秒。
2. 早期版本滚动范围（自算行数）和绘制（Qt 换行）不一致，往下滚会一片空白。
"""
from __future__ import annotations

import math
import time

from PyQt5.QtCore import (QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal)
from PyQt5.QtGui import (QColor, QFont, QFontMetricsF, QPainter, QPainterPath,
                         QPen, QPolygonF)
from PyQt5.QtWidgets import QWidget

MAX_STROKE_PX = 2.6          # 描边半径上限，防止"偏移复制"把字撑成墨团


# ---------------------------------------------------------------- 颜色工具
def qcolor(value) -> QColor:
    if isinstance(value, QColor):
        return QColor(value)
    return QColor(value)


def alpha(rgb, a: float) -> QColor:
    """把一个 RGB 加上统一透明度。"""
    r, g, b = (rgb if isinstance(rgb, (tuple, list)) else
               (rgb.red(), rgb.green(), rgb.blue()))
    return QColor(int(r), int(g), int(b), max(0, min(255, int(a * 255))))


def stroke_scale(opacity: float) -> float:
    """底色越透明，描边越要重一点才看得见。"""
    return 1.0 if opacity <= 0.01 else max(0.6, min(1.6, 1.0 / max(0.2, opacity)))


def make_font(size: int, bold: bool = False) -> QFont:
    font = QFont("Microsoft YaHei UI")
    font.setPixelSize(int(size))
    font.setBold(bool(bold))
    return font


# ---------------------------------------------------------------- 圆角面板
class RoundedPanel(QWidget):
    """圆角底色板。本项目把填充和描边都设为全 0，即完全透明、无边框。"""

    def __init__(self, radius: int = 12, parent=None):
        super().__init__(parent)
        self._radius = int(radius)
        self._fill = QColor(255, 255, 255, 0)
        self._stroke = QColor(0, 0, 0, 0)
        self._stroke_width = 0.0

    def set_fill(self, color) -> None:
        self._fill = qcolor(color)
        self.update()

    def set_stroke(self, color, width: float = 1.0) -> None:
        self._stroke = qcolor(color)
        self._stroke_width = float(width)
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, self._radius, self._radius)
        if self._fill.alpha() > 0:
            painter.fillPath(path, self._fill)
        if self._stroke_width > 0 and self._stroke.alpha() > 0:
            pen = QPen(self._stroke)
            pen.setWidthF(self._stroke_width)
            painter.setPen(pen)
            painter.drawPath(path)
        else:
            painter.setPen(Qt.NoPen)


# ---------------------------------------------------------------- 正文
class StrokedText(QWidget):
    """正文：增量换行 + 按可见行绘制 + 滚动。

    对外接口：
        text() / set_text() / append_text()
        scroll_to_extreme("top"|"bottom") / set_scroll()
        max_scroll() / content_height()
        set_text_color() / set_font_size() / set_bold()
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._text = ""
        self._font_size = 8
        self._bold = False
        self._fg = QColor(30, 30, 30, 255)
        self._stroke_rgb = (255, 255, 255)
        self._stroke_width = 0.0
        self._stroke_alpha = 235
        self._padding = 8
        self._scroll = 0.0
        # --- 增量换行：流式追加时只处理新增字符 ---
        self._lines: list[tuple[int, int]] = []   # [(start, end), ...] 最后一项是当前行
        self._wrapped_from = 0                    # 已经处理到的字符位置
        self._line_start = 0                      # 当前行的起始字符下标
        self._line_used = 0.0                     # 当前行已占用的宽度
        self._m_width = 0.0
        self._m_metrics: QFontMetricsF | None = None
        self._m_line_height = 0.0
        self._m_cache_key = None
        self.setMouseTracking(True)
        # 合并重绘：流式期间 40ms 一次，避免每个 token 都触发一次 paint
        self._paint_timer = QTimer(self)
        self._paint_timer.setSingleShot(True)
        self._paint_timer.setInterval(40)
        self._paint_timer.timeout.connect(self._paint_now)

    # ---------------- 换行与绘制 ----------------
    def _metrics(self):
        """字体度量缓存：字号或加粗变化时才重建。"""
        key = (self._font_size, self._bold)
        if self._m_cache_key != key or self._m_metrics is None:
            font = make_font(self._font_size, self._bold)
            self._m_metrics = QFontMetricsF(font)
            self._m_line_height = self._m_metrics.lineSpacing()
            self._m_cache_key = key
            self._reset_measure()
        return self._m_metrics

    def _reset_lines(self) -> None:
        """行表作废，从头重量（宽度变了才需要）。"""
        self._wrapped_from = 0
        self._lines = []
        self._line_start = 0
        self._line_used = 0.0

    def _reset_measure(self) -> None:
        """宽度/字号变了，行表作废，下次重新量。"""
        self._m_width = 0.0
        self._reset_lines()
        self._scroll = 0.0
        self.updateGeometry()
        self.update()

    def _usable_width(self) -> float:
        return max(1.0, float(self.width()) - self._padding * 2)

    def _wrap(self) -> list[tuple[int, int]]:
        """增量换行：只处理 _wrapped_from 之后的新字符。

        行表 `self._lines` 是 (start, end) 字符下标列表，**最后一项是正在填的那一行**。
        断行时做两件事：把当前行的 end 定死，再 append 一个新行。

        踩过的坑：早先写成 `self._lines[-1] = (start, i)`，那是在**覆盖**最后一行而不是追加，
        结果整篇文本永远只有 1 行，内容高度恒为 11px。
        另外必须显式处理 '\\n'——模型输出几乎全是硬换行，忽略它排版就全乱。

        游标 (_wrapped_from / _line_start / _line_used) 让追加的边际成本
        与新增字符数成正比，流式输出时不会退化成 O(n^2)。
        """
        metrics = self._metrics()
        width = self._usable_width()
        if width != self._m_width:
            # 宽度变了必须整篇重量
            self._m_width = width
            self._reset_lines()

        if not self._lines:
            self._lines = [(0, 0)]
            self._line_start = 0
            self._line_used = 0.0

        text = self._text
        i = self._wrapped_from
        n = len(text)
        while i < n:
            ch = text[i]
            if ch == "\n":
                # 硬换行：当前行到 i 结束（不含换行符本身），新行从 i+1 开始
                self._lines[-1] = (self._line_start, i)
                self._lines.append((i + 1, i + 1))
                self._line_start = i + 1
                self._line_used = 0.0
                i += 1
                continue
            advance = metrics.horizontalAdvance(ch)
            if self._line_used + advance > width and i > self._line_start:
                # 放不下 -> 当前行到 i 结束，开新行
                self._lines[-1] = (self._line_start, i)
                self._lines.append((i, i))
                self._line_start = i
                self._line_used = 0.0
                continue
            self._line_used += advance
            i += 1

        # 最后一行补齐（文本以 \\n 结尾时这就是那个空的尾行）
        self._lines[-1] = (self._line_start, n)
        self._wrapped_from = n
        return self._lines

    # ---------------- 对外接口 ----------------
    def text(self) -> str:
        return self._text

    def set_text(self, text: str) -> None:
        self._text = text
        self._reset_measure()
        self.update()

    def append_text(self, chunk: str) -> None:
        """流式追加：增量换行 + 合并重绘，并让视图跟随底部。"""
        self._text += chunk
        self._wrap()                       # 增量更新行表
        self._scroll = self.max_scroll()   # 贴底；与绘制同源，不会滚过头出现空白
        if not self._paint_timer.isActive():
            self._paint_timer.start()

    def set_font_size(self, size: int) -> None:
        self._font_size = int(size)
        self._reset_measure()
        self.updateGeometry()
        self.update()

    def set_bold(self, bold: bool) -> None:
        self._bold = bool(bold)
        self._reset_measure()
        self.update()

    def set_text_color(self, color) -> None:
        self._fg = qcolor(color)
        self.update()

    def set_stroke(self, rgb, width: float, alpha_value: int = 235) -> None:
        self._stroke_rgb = rgb
        self._stroke_width = float(width)
        self._stroke_alpha = int(alpha_value)
        self.update()

    def scroll_to_extreme(self, where: str) -> None:
        self._scroll = 0.0 if where == "top" else self.max_scroll()
        self.update()

    def scroll_by(self, delta: float) -> None:
        target = max(0.0, min(self.max_scroll(), self._scroll + delta))
        if abs(target - self._scroll) > 0.01:
            self._scroll = target
            self.update()

    def set_scroll(self, value: float) -> None:
        self._scroll = max(0.0, min(self.max_scroll(), value))

    def content_height(self) -> float:
        """真实内容高度 = 行数 × 行高（与绘制完全一致）。"""
        return len(self._wrap()) * self._m_line_height

    def max_scroll(self) -> float:
        self._metrics()
        content = self.content_height()
        return max(0.0, int(content) - (self.height() - self._padding * 2))

    def sizeHint(self) -> QSize:
        width = max(1, self.width() - self._padding * 2)
        return QSize(max(1, int(width)),
                     int(self.content_height()) + self._padding * 2)

    def minimumSizeHint(self) -> QSize:
        return QSize(60, 40)

    def _paint_now(self) -> None:
        self.update()

    # ---------------- 绘制 ----------------
    def paintEvent(self, _event):
        """只画"看得见"的那些行 —— 与换行表共用同一套结果，成本恒定。"""
        if not self._text:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        painter.setFont(make_font(self._font_size, self._bold))
        painter.setBrush(Qt.NoBrush)

        lines = self._wrap()
        line_height = self._m_line_height
        view_height = self.height() - self._padding * 2
        first = max(0, int(self._scroll // line_height))
        last = min(len(lines), first + int(view_height // line_height) + 2)
        left = float(self._padding)
        right = float(self.width() - self._padding)

        radius = min(self._stroke_width / 2.0, MAX_STROKE_PX)
        offsets = []
        if radius > 0.15:
            steps = 8 if radius <= 1.4 else 12
            offsets = [(radius * math.cos(2 * math.pi * i / steps),
                        radius * math.sin(2 * math.pi * i / steps))
                       for i in range(steps)]
            outline = QColor(*self._stroke_rgb)
            outline.setAlpha(self._stroke_alpha)
            outline_pen = QPen(outline)
            outline_pen.setWidthF(1.0)
            fill_pen = QPen(self._fg)
        else:
            fill_pen = QPen(self._fg)

        for index in range(first, last):
            start, end = lines[index]
            segment = self._text[start:end]
            if not segment:
                continue
            top = self._padding + index * line_height - self._scroll
            rect = QRectF(left, top, right - left, line_height)
            if offsets:
                # 描边：多方向偏移复制。注意这会让绘制调用 ×8~12，
                # 所以默认关闭；真要开建议改用 QPainterPath.addText + strokePath。
                painter.setPen(outline_pen)
                for dx, dy in offsets:
                    painter.drawText(rect.translated(dx, dy),
                                     int(Qt.AlignLeft | Qt.AlignVCenter), segment)
                painter.setPen(fill_pen)
            painter.drawText(rect, int(Qt.AlignLeft | Qt.AlignVCenter), segment)

    def wheelEvent(self, event):
        """一格滚轮 = 120 单位 → 45px（规格 4.5）。

        正负号：滚轮往下（angleDelta > 0）内容应该往上走，scroll 增大。
        之前写成 `-angleDelta * 0.375` 把方向弄反了，滚轮上下颠倒。
        """
        self.scroll_by(event.angleDelta().y() * 0.375)
        event.accept()


# ---------------------------------------------------------------- 悬停按钮
class HoverButton(QWidget):
    """悬停到设定秒数自动触发，点击永远无效。底部有一条细进度线。"""

    FONT_SIZE = 8
    hover_triggered = pyqtSignal()
    cursor_moved = pyqtSignal()

    def __init__(self, text: str, delay_ms: int = 1000, parent=None):
        super().__init__(parent)
        self._text = text
        self.delay_ms = int(delay_ms)
        self.progress = 0.0
        self._hovering = False
        self._armed = False
        self._fg = QColor(0, 0, 0, 255)
        self.setMouseTracking(True)
        self._ticker = QTimer(self)
        self._ticker.setInterval(50)
        self._ticker.timeout.connect(self._tick)

    # ---------------- 对外 ----------------
    def set_text(self, text: str) -> None:
        self._text = text
        self.update()

    def set_fg(self, color) -> None:
        self._fg = qcolor(color)
        self.update()

    def arm(self) -> None:
        self._armed = True
        self._maybe_start()

    def disarm(self) -> None:
        self._armed = False
        self._cancel()

    def set_active(self, active: bool) -> None:
        """临时停用（例如请求进行中），避免重复触发。"""
        self.setEnabled(active)
        if not active:
            self._cancel()

    # ---------------- 悬停计时 ----------------
    def _maybe_start(self) -> None:
        if self._hovering and self._armed and self.isEnabled() and self.delay_ms > 0:
            self.progress = 0.001
            self._ticker.start()

    def _cancel(self) -> None:
        self.progress = 0.0
        self._hovering = False
        self._ticker.stop()
        self.update()

    def _tick(self) -> None:
        if not (self._hovering and self._armed and self.isEnabled()):
            self._ticker.stop()
            self.progress = 0.0
            self.update()
            return
        step = self._ticker.interval() / float(max(1, self.delay_ms))
        new_progress = self.progress + step
        # 变化不足 2% 不重绘，20fps 的进度线够用了
        if abs(new_progress - self.progress) >= 0.02 or new_progress >= 1.0:
            self.progress = min(1.0, new_progress)
            self.update()
        if self.progress >= 1.0:
            self._ticker.stop()
            self._hovering = False
            self.hover_triggered.emit()

    # ---------------- 事件 ----------------
    def enterEvent(self, event):
        self._hovering = True
        self._maybe_start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._cancel()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        """点击永远无效——这是设计要求，不是遗漏。"""
        event.ignore()

    def mouseMoveEvent(self, event):
        self.cursor_moved.emit()
        super().mouseMoveEvent(event)

    def fit_font(self):
        """字号按可用宽度自动缩放，保证文字始终完整。"""
        available = max(10, self.width() - 10)
        size = self.FONT_SIZE
        font = make_font(size, True)
        while size > 5:
            if QFontMetricsF(font).horizontalAdvance(self._text) <= available:
                break
            size -= 1
            font = make_font(size, True)
        return font

    def sizeHint(self):
        metrics = QFontMetricsF(make_font(self.FONT_SIZE, True))
        return QSize(int(metrics.horizontalAdvance(self._text) + 14), 24)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        rect = QRectF(self.rect())

        color = QColor(self._fg)          # 与背景板同一套颜色，不额外加黑
        if not self.isEnabled():
            color.setAlpha(max(0, color.alpha() // 2))
        painter.setPen(QPen(color))
        painter.setFont(self.fit_font())
        painter.drawText(rect, int(Qt.AlignCenter), self._text)

        # 底部细进度线：颜色与背景板同源
        if self.progress > 0.001:
            line = QRectF(rect.left(), rect.bottom() - 1.0,
                          rect.width() * min(1.0, self.progress), 1.0)
            painter.fillRect(line, color)


# ---------------------------------------------------------------- 小圆圈
class DragMarker(QWidget):
    """左上角小圆圈：悬停 N 秒进入"跟随鼠标移动"模式。"""

    FONT_SIZE = 8
    hover_triggered = pyqtSignal()
    cursor_moved = pyqtSignal()

    def __init__(self, delay_ms: int = 2000, parent=None):
        super().__init__(parent)
        self.delay_ms = int(delay_ms)
        self.progress = 0.0
        self._hovering = False
        self._armed = False
        self._fg = QColor(0, 0, 0, 255)
        self.setFixedSize(14, 14)
        self.setMouseTracking(True)
        self._ticker = QTimer(self)
        self._ticker.setInterval(50)
        self._ticker.timeout.connect(self._tick)

    def set_fg(self, color) -> None:
        self._fg = qcolor(color)
        self.update()

    def arm(self) -> None:
        self._armed = True
        self._maybe_start()

    def disarm(self) -> None:
        self._armed = False
        self._cancel()

    def _maybe_start(self) -> None:
        if self._hovering and self._armed and self.delay_ms > 0:
            self.progress = 0.001
            self._ticker.start()

    def _cancel(self) -> None:
        self.progress = 0.0
        self._hovering = False
        self._ticker.stop()
        self.update()

    def _tick(self) -> None:
        if not (self._hovering and self._armed):
            self._ticker.stop()
            self.progress = 0.0
            self.update()
            return
        self.progress = min(1.0, self.progress
                            + self._ticker.interval() / float(max(1, self.delay_ms)))
        self.update()
        if self.progress >= 1.0:
            self._ticker.stop()
            self._hovering = False
            self.hover_triggered.emit()

    def enterEvent(self, event):
        self._hovering = True
        self._maybe_start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._cancel()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        event.ignore()

    def mouseMoveEvent(self, event):
        self.cursor_moved.emit()
        super().mouseMoveEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        color = QColor(self._fg)
        rect = QRectF(self.rect())
        inner = rect.adjusted(3.0, 3.0, -3.0, -3.0)

        if self.progress > 0.001 and self.progress < 1.0:
            # 悬停计满过程中：空心圆 + 进度弧
            painter.setPen(QPen(color))
            painter.setBrush(Qt.NoBrush)
            painter.drawArc(inner, 90 * 16,
                            int(-360 * 16 * min(1.0, self.progress)))
        elif self.progress >= 1.0:
            # 计满 = 进入跟随模式：实心
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
        else:
            # 常态：规格要求「纯色实心圆点，无描边」
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
        painter.drawEllipse(inner)


# ---------------------------------------------------------------- 滚动箭头
class ScrollArrow(QWidget):
    """上下滚动箭头：悬停时以固定节奏持续滚动。

    step_px / step_ms 控制节奏（默认 10px / 50ms ≈ 200px/s）。
    """

    scrolled = pyqtSignal(float)

    def __init__(self, direction: str = "up", parent=None):
        super().__init__(parent)
        self.direction = direction
        self.step_px = 10
        self.step_ms = 50
        self._fg = QColor(0, 0, 0, 255)
        self._hovering = False
        # 由外部在滚动后回填：这个方向是否已经到头（到头就停表，不空转）
        self._at_bound = False
        self.setMouseTracking(True)
        self._ticker = QTimer(self)
        self._ticker.setInterval(self.step_ms)
        self._ticker.timeout.connect(self._tick)

    def notify_position(self, at_top: bool, at_bottom: bool) -> None:
        """主窗口滚动后告知当前边界状态。

        到头后计时器已经停了；一旦又离开边界（内容变长/变短），要把
        `_at_bound` 复位，否则下次悬停会一动不动。
        """
        self._at_bound = at_top if self.direction == "up" else at_bottom

    def set_fg(self, color) -> None:
        self._fg = qcolor(color)
        self.update()

    def _tick(self) -> None:
        """持续滚动。规格（4.5）：已经到顶/底时计时器自动停止，不在边界空转。"""
        if not self._hovering:
            self._ticker.stop()
            return
        if self._at_bound:
            self._ticker.stop()
            return
        delta = -self.step_px if self.direction == "up" else self.step_px
        self.scrolled.emit(float(delta))

    def enterEvent(self, event):
        self._hovering = True
        self._ticker.start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovering = False
        self._ticker.stop()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        event.ignore()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        color = QColor(self._fg)
        if not self.isVisible():
            return
        pen = QPen(color)
        pen.setWidthF(1.2)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)

        w = float(self.width())
        h = float(self.height())
        # 几何画法：up 是尖朝上（∧），down 是尖朝下（∨）
        if self.direction == "up":
            points = QPolygonF([QPointF(w * 0.5, h * 0.25),
                                QPointF(w * 0.78, h * 0.70),
                                QPointF(w * 0.22, h * 0.70)])
        else:
            points = QPolygonF([QPointF(w * 0.5, h * 0.75),
                                QPointF(w * 0.78, h * 0.30),
                                QPointF(w * 0.22, h * 0.30)])
        painter.drawPolygon(points)