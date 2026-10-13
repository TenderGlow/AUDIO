"""悬浮主窗口：无边框、置顶、可拖动、可拖边缩放 + 悬停触发。

布局（默认 268×219）：
    ┌ ● ─────────────────── 已加页 2/4 复制 ┐   ← ● = 跟随移动标记
    │                ▲                     │
    │ 答案正文                            │   ← 正文上下边缘正中间的滚动箭头
    │                ▼                     │
    │ 解题  加页  重置  退出                │   ← 每个按钮底部有一条细进度线
    └──────────────────────────────────────┘
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

from PyQt5.QtCore import (QEvent, QPoint, QRect, Qt, QThreadPool, QTimer,
                         pyqtSignal)
from PyQt5.QtGui import QColor, QCursor, QGuiApplication
from PyQt5.QtWidgets import (QApplication, QHBoxLayout, QLabel, QVBoxLayout,
                             QWidget)

from .capture import Capture, CaptureWorker
from .config import Settings, save_credentials
from .detect import capture_risk, reset_poll_counter
from .hotkeys import (HotkeyManager, WM_HOTKEY, exclude_window_from_capture,
                      foreground_process_name, message_fields)
from .llm import SolveWorker
from .theme import MIN_SIZE, get_theme
from .widgets import (DragMarker, HoverButton, RoundedPanel, ScrollArrow,
                      StrokedText, alpha, make_font, stroke_scale)

EDGE = 8          # 边缘缩放感应区宽度(px)，规格见设计文档 7.2
TITLE_BAR = 22    # 顶部标题条高度(px)

# 界面上不写任何功能说明，正文留空，等大模型给结果


class _POINT(ctypes.Structure):
    """Win32 POINT；模块级定义一次即可，不要放进被高频调用的函数里。"""
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


_user32 = ctypes.windll.user32 if hasattr(ctypes, "windll") else None


class DragProxy(QWidget):
    """标题条：把鼠标事件转发给主窗口，实现"按住这里能拖动"。"""

    forwarded = pyqtSignal(object)

    def mousePressEvent(self, event):
        self.forwarded.emit(event)

    def mouseMoveEvent(self, event):
        self.forwarded.emit(event)

    def mouseReleaseEvent(self, event):
        self.forwarded.emit(event)

    def mouseDoubleClickEvent(self, event):
        self.forwarded.emit(event)


class FloatingWindow(QWidget):
    def __init__(self, settings: Settings | None = None):
        super().__init__()
        self.settings = settings or Settings()

        self._pages: list[Capture] = []
        self._busy = False
        self._reason_text = ""
        self._started_at = 0.0
        self._move_mode = False
        self._move_offset = QPoint(0, 0)
        self._last_cursor = QPoint(0, 0)
        self._last_move_ts = 0.0
        self._was_on_top = self.settings.always_on_top

        # 拖动 / 缩放状态。
        # 这些必须在 _build_ui 之前初始化：mouseMoveEvent 一进来就读 _resize_mode /
        # _dragging，没初始化会抛 AttributeError；而 PyQt 里事件回调抛异常会走
        # qFatal 直接 abort —— 表现为"鼠标一动进窗口就闪退"（事件日志里
        # Qt5Core.dll / 0xc0000409 就是这个）。
        self._dragging = False
        self._drag_offset = QPoint(0, 0)
        self._drag_pending = False
        self._resize_mode = False
        self._resize_flags = ""
        self._resize_geo = QRect(self.geometry())
        self._resize_start = QPoint(0, 0)

        flags = Qt.FramelessWindowHint | Qt.Tool
        if self.settings.always_on_top:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)
        self.setMinimumSize(*MIN_SIZE)
        self.resize(self.settings.width, self.settings.height)
        # 标题留空：Chrome 等的"共享某个窗口"选择器是靠枚举顶层窗口 + 取标题工作的，
        # 无标题窗口会被直接跳过。留着标题就等于把自己送到选择器列表里。
        # 外部停止程序改用 PID 精确匹配（见 audio/shutdown.py），不依赖标题。
        self.setWindowTitle("")

        self._build_ui()
        self.apply_settings(self.settings, persist=False)

        # 抓屏/录屏时把自己排除在外：既不会被截进来，也不会出现在屏幕共享里。
        # 这是硬保证（系统级捕获层面生效），detect.py 的主动隐藏只是提前规避残影。
        self._apply_capture_exclusion()

        # 后台热键：界面上不显示、不说明
        self.hotkeys = HotkeyManager(self)
        self.hotkeys.show_requested.connect(self._show_by_hotkey)
        self.hotkeys.hide_requested.connect(self._hide_by_hotkey)
        self.hotkeys.quit_requested.connect(self.on_quit)
        self.hotkeys.bind(int(self.winId()), self.settings.hotkey_show,
                          self.settings.hotkey_hide, self.settings.hotkey_quit)

        # 跟随移动的定时器
        self._move_timer = QTimer(self)
        self._move_timer.setInterval(30)
        self._move_timer.timeout.connect(self._move_tick)

        # 状态栏节流窗口：到点才把攒下的状态刷出来
        self._status_ts = 0.0
        self._status_pending: str | None = None
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(self.settings.status_throttle_ms)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(self._flush_status)

        # 箭头刷新：流式期间复用同一个定时器，不再每个 token 新建一个 singleShot
        self._arrow_timer = QTimer(self)
        self._arrow_timer.setInterval(160)
        self._arrow_timer.setSingleShot(True)
        self._arrow_timer.timeout.connect(self._refresh_arrows)

        # 防录屏 / 防截屏轮询
        self._self_hidden = False
        self._was_visible_before_hide = False
        self._capture_timer = QTimer(self)
        self._capture_timer.setInterval(self.settings.capture_check_ms)
        self._capture_timer.timeout.connect(self._check_capture_risk)
        if self.settings.anti_capture:
            self._capture_timer.start()
            reset_poll_counter()     # 启动后第一次就做全量扫描

    # ------------------------------------------------------------------
    # 界面
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(EDGE, EDGE, EDGE, EDGE)

        # 底色板：与背景浑然一体——不描边、不加任何修饰
        self.panel = RoundedPanel(radius=12)
        self.panel.setMouseTracking(True)
        self.panel.set_fill(QColor(0, 0, 0, 0))
        self.panel.set_stroke(QColor(0, 0, 0, 0), 0)
        outer.addWidget(self.panel)

        layout = QVBoxLayout(self.panel)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        # 顶部：左上角小圆圈（移动触发）+ 状态
        self.bar = DragProxy()
        self.bar.setFixedHeight(TITLE_BAR)
        self.bar.setMouseTracking(True)
        self.bar.forwarded.connect(self._on_proxy)
        bar_layout = QHBoxLayout(self.bar)
        bar_layout.setContentsMargins(0, 0, 0, 0)
        bar_layout.setSpacing(6)

        # 左上角的小圆圈：悬停 2 秒进入跟随移动
        self.marker = DragMarker(self.settings.marker_delay_ms)
        self.marker.hover_triggered.connect(self.start_move_mode)
        bar_layout.addWidget(self.marker)

        self.status = QLabel("")
        self.status.setFont(make_font(self.settings.font_status_size, False))
        self.status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.status.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        bar_layout.addWidget(self.status)
        bar_layout.addStretch(1)

        # 顶栏右侧的「复制」：答案唯一的出口（底部固定四个按钮，不塞第五个）
        self.btn_copy = HoverButton("复制", self.settings.copy_delay_ms)
        self.btn_copy.FONT_SIZE = self.settings.font_button_size
        self.btn_copy.setFixedHeight(14)
        self.btn_copy.hover_triggered.connect(self.on_copy)
        bar_layout.addWidget(self.btn_copy)

        layout.addWidget(self.bar)

        # 正文区：上边缘正中间的"向上"箭头 + 正文 + 下边缘正中间的"向下"箭头
        body_area = QWidget()
        body_area.setMouseTracking(True)
        body_layout = QVBoxLayout(body_area)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        self.arrow_up = ScrollArrow("up")
        self.arrow_up.setFixedSize(self.settings.arrow_size,
                                   self.settings.arrow_size)
        self.arrow_up.step_px = self.settings.arrow_step_px
        self.arrow_up.step_ms = self.settings.arrow_step_ms
        self.arrow_up.scrolled.connect(self._on_arrow_scroll)
        body_layout.addWidget(self.arrow_up, 0, Qt.AlignHCenter)

        self.body = StrokedText()
        body_layout.addWidget(self.body, 1)

        self.arrow_down = ScrollArrow("down")
        self.arrow_down.setFixedSize(self.settings.arrow_size,
                                     self.settings.arrow_size)
        self.arrow_down.step_px = self.settings.arrow_step_px
        self.arrow_down.step_ms = self.settings.arrow_step_ms
        self.arrow_down.scrolled.connect(self._on_arrow_scroll)
        body_layout.addWidget(self.arrow_down, 0, Qt.AlignHCenter)

        self.arrow_up.hide()
        self.arrow_down.hide()
        layout.addWidget(body_area, 1)

        # 底部：四个按钮
        bottom = QWidget()
        bottom.setMouseTracking(True)
        bottom_layout = QHBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(4)

        self.btn_solve = HoverButton("解题", self.settings.solve_delay_ms)
        self.btn_solve.hover_triggered.connect(self.on_solve)
        self.btn_add = HoverButton("加页", self.settings.add_page_delay_ms)
        self.btn_add.hover_triggered.connect(self.on_add_page)
        self.btn_reset = HoverButton("重置", self.settings.reset_delay_ms)
        self.btn_reset.hover_triggered.connect(self.on_reset)
        self.btn_quit = HoverButton("退出", self.settings.quit_delay_ms)
        self.btn_quit.hover_triggered.connect(self.on_quit)
        for button in (self.btn_solve, self.btn_add, self.btn_reset, self.btn_quit):
            button.FONT_SIZE = self.settings.font_button_size
            bottom_layout.addWidget(button, 1)

        layout.addWidget(bottom)
        # 界面上不留任何说明文字，正文留空等结果
        self.body.set_text("")

        self._hover_buttons = (self.btn_solve, self.btn_add, self.btn_reset,
                               self.btn_quit)
        # 复制与重置一样随时可用，不受请求忙闲影响
        self._arm_targets = self._hover_buttons + (self.marker, self.btn_copy)
        for button in self._arm_targets:
            button.cursor_moved.connect(self._arm_hover_buttons)

    def _arm_hover_buttons(self) -> None:
        for button in self._arm_targets:
            try:
                button.cursor_moved.disconnect(self._arm_hover_buttons)
            except TypeError:
                pass
            button.arm()

    def _disarm_hover_buttons(self) -> None:
        for button in self._arm_targets:
            button.disarm()
            button.cursor_moved.connect(self._arm_hover_buttons)

    # ------------------------------------------------------------------
    # 外观
    # ------------------------------------------------------------------
    def apply_settings(self, settings: Settings, persist: bool = True) -> None:
        self.settings = settings
        theme = get_theme(settings.theme)

        self.body.set_font_size(settings.font_size)
        self.body.set_bold(settings.bold)
        self.body.set_stroke(theme.stroke,
                             settings.stroke_width * stroke_scale(settings.bg_opacity),
                             settings.stroke_alpha)

        # 所有可见元素共用同一支"墨水"：同一个颜色、同一档透明度，不做任何彩色区分。
        # 注意必须用 HexArgb：QColor.name() 默认会丢掉 alpha，文字就变成纯黑。
        ink = alpha(theme.fg, settings.text_opacity)
        self.body.set_text_color(ink)
        self.status.setStyleSheet(
            f"color: {ink.name(QColor.NameFormat.HexArgb)}; background: transparent;")
        for button in (*self._hover_buttons, self.marker, self.btn_copy):
            button.set_fg(ink)
        self.arrow_up.set_fg(ink)
        self.arrow_down.set_fg(ink)

        for button, delay in ((self.btn_solve, settings.solve_delay_ms),
                              (self.btn_add, settings.add_page_delay_ms),
                              (self.btn_reset, settings.reset_delay_ms),
                              (self.btn_quit, settings.quit_delay_ms),
                              (self.btn_copy, settings.copy_delay_ms)):
            button.delay_ms = delay
        self.marker.delay_ms = settings.marker_delay_ms

        if self._was_on_top != settings.always_on_top:
            self._was_on_top = settings.always_on_top
            visible = self.isVisible()
            self.setWindowFlag(Qt.WindowStaysOnTopHint, settings.always_on_top)
            if visible:
                self.show()

        hotkeys = getattr(self, "hotkeys", None)
        if hotkeys is not None:
            hotkeys.bind(int(self.winId()), settings.hotkey_show,
                         settings.hotkey_hide, settings.hotkey_quit)

        if persist:
            save_credentials(settings)

    # ------------------------------------------------------------------
    # 移动 / 缩放
    # ------------------------------------------------------------------
    def _edge_at(self, pos: QPoint) -> str:
        """命中哪几条边。返回规范化后的组合（"lt"/"rb"/"l"/…），空串表示没命中。"""
        left = pos.x() <= EDGE
        right = pos.x() >= self.width() - EDGE
        top = pos.y() <= EDGE
        bottom = pos.y() >= self.height() - EDGE
        # 规范顺序：先左右(l/r) 再上下(t/b)，这样 "l"+"t" -> "lt"
        mode = ""
        if left:
            mode += "l"
        if right:
            mode += "r"
        if top:
            mode += "t"
        if bottom:
            mode += "b"
        return mode

    # 四边 + 四角共 8 种光标。键是**排序后**的组合，
    # 因为 _cursor_for 会先 sorted() 再查表（"lb" 排序后是 "bl"）。
    _CURSORS = {
        "l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor,
        "t": Qt.SizeVerCursor, "b": Qt.SizeVerCursor,
        "lt": Qt.SizeBDiagCursor, "rt": Qt.SizeFDiagCursor,
        "bl": Qt.SizeFDiagCursor, "br": Qt.SizeBDiagCursor,
    }

    def _cursor_for(self, mode: str) -> Qt.CursorShape:
        if not mode:
            return Qt.ArrowCursor
        # 角上的两个字母必须转成规范顺序再查表：l+t -> "lt"，t+l -> 同样是 "lt"
        return self._CURSORS.get("".join(sorted(mode)), Qt.ArrowCursor)

    def mouseMoveEvent(self, event):
        mode = self._edge_at(event.pos())
        self.setCursor(self._cursor_for(mode))
        if self._resize_mode:
            self._do_resize(event.pos(), event.globalPos())
            return
        if self._dragging:
            self._do_drag(event.globalPos())
            return
        # 正文区作为拖动热区
        if event.buttons() & Qt.LeftButton and not self._move_mode:
            self._dragging = True
            self._drag_offset = event.globalPos() - self.pos()
            self._disarm_hover_buttons()

    def mousePressEvent(self, event):
        mode = self._edge_at(event.pos())
        if mode:
            self._resize_mode = True
            self._resize_flags = mode          # "l"/"r"/"t"/"b"/"lt"/"rb"/...
            self._resize_geo = QRect(self.geometry())
            self._resize_start = event.globalPos()
        else:
            self._dragging = False

    def mouseReleaseEvent(self, _event):
        self._resize_mode = False
        self._resize_flags = ""
        self._dragging = False
        QTimer.singleShot(0, self._refresh_arrows)

    def leaveEvent(self, event):
        self.unsetCursor()
        super().leaveEvent(event)

    def _do_drag(self, global_pos: QPoint) -> None:
        self.move(global_pos - self._drag_offset)
        if self._move_mode:
            self._move_ts = time.monotonic()

    def _do_resize(self, pos: QPoint, global_pos: QPoint) -> None:
        """按文档规格：四边 + 四角共 8 种逻辑，下限 180×140。

        之前这版是空壳（只认右下），拖左边/上边完全没反应。
        这里按"按下时的几何 + 当前位移"重新解算四条边。
        """
        geo = self._resize_geo
        left, top = geo.left(), geo.top()
        right, bottom = geo.right(), geo.bottom()
        flags = self._resize_flags
        dx = global_pos.x() - self._resize_start.x()
        dy = global_pos.y() - self._resize_start.y()

        min_w, min_h = MIN_SIZE
        if "l" in flags:
            new_left = left + dx
            if right - new_left + 1 >= min_w:      # right() 是闭区间，宽度要 +1
                left = new_left
        if "r" in flags:
            new_right = right + dx
            if new_right - left + 1 >= min_w:
                right = new_right
        if "t" in flags:
            new_top = top + dy
            if bottom - new_top + 1 >= min_h:
                top = new_top
        if "b" in flags:
            new_bottom = bottom + dy
            if new_bottom - top + 1 >= min_h:
                bottom = new_bottom

        self.setGeometry(QRect(left, top, right - left + 1, bottom - top + 1))
        QTimer.singleShot(0, self._refresh_arrows)

    def _on_proxy(self, event) -> None:
        """标题条事件转发：按住拖动。"""
        if event.type() == QEvent.MouseButtonPress:
            self._dragging = False
            self._drag_pending = True
        elif event.type() == QEvent.MouseMove:
            if getattr(self, "_drag_pending", False):
                self._dragging = True
                self._drag_offset = event.globalPos() - self.pos()
                self._drag_pending = False
                self._disarm_hover_buttons()
            if self._dragging:
                self._do_drag(event.globalPos())
        elif event.type() == QEvent.MouseButtonRelease:
            self._dragging = False
            self._drag_pending = False

    # ------------------------------------------------------------------
    # 跟随移动
    # ------------------------------------------------------------------
    def start_move_mode(self) -> None:
        self._move_mode = True
        self._last_cursor = self._cursor_pos()
        self._move_offset = self._last_cursor - self.pos()
        self._last_move_ts = time.monotonic()
        self._move_timer.start()
        self._set_status("跟随移动中", force=True)

    @staticmethod
    def _cursor_pos() -> QPoint:
        """取真实鼠标位置；QCursor 在部分环境下会返回哨兵值，改用 Win32 API。

        结构体提到模块级：跟随移动时每 30ms 调一次，
        在函数体里现定义 ctypes.Structure 是没必要的重复劳动。
        """
        point = _POINT()
        if _user32 and _user32.GetCursorPos(ctypes.byref(point)):
            return QPoint(int(point.x), int(point.y))
        return QCursor.pos()

    def _move_tick(self) -> None:
        if not self._move_timer.isActive():
            return
        cursor = self._cursor_pos()
        # 位移超过 2px 才算"真的在移动"，避免 1px 抖动一直续命
        delta = (cursor - self._last_cursor).manhattanLength()
        if delta > 2:
            self._last_cursor = cursor
            self.move(cursor - self._move_offset)
            self._last_move_ts = time.monotonic()
        elif time.monotonic() - self._last_move_ts > self.settings.marker_stop_ms / 1000.0:
            self._move_timer.stop()
            self._move_mode = False
            self._disarm_hover_buttons()
            self._set_status("已停止跟随", force=True)

    # ------------------------------------------------------------------
    # 防录屏 / 防截屏
    # ------------------------------------------------------------------
    def _apply_capture_exclusion(self) -> bool:
        """把窗口从系统级捕获里排除（WDA_EXCLUDEFROMCAPTURE）。

        实测：普通窗口设 0x11 后 BitBlt 抓到的像素从 40000 降到 0，
        即窗口在截图/录屏/屏幕共享里彻底消失，本地显示不受影响。
        """
        if not self.settings.exclude_from_capture:
            return False
        return exclude_window_from_capture(int(self.winId()))

    def _check_capture_risk(self) -> None:
        """检测到录屏/截屏就自动隐藏，撤掉后自动回来。

        兜底已有 `WDA_EXCLUDEFROMCAPTURE`，这一层是为了避免某些捕获工具
        把窗口画成一个黑块或残影——提前躲开更干净。
        """
        if not self.settings.anti_capture:
            return
        risky, _reason = capture_risk()
        if risky and not self._self_hidden:
            self._was_visible_before_hide = self.isVisible()
            self._self_hidden = True
            if self._was_visible_before_hide:
                self.hide()
        elif not risky and self._self_hidden:
            self._self_hidden = False
            if self._was_visible_before_hide:
                self.show()
                self._force_topmost()

    def _ensure_no_taskbar(self) -> None:
        """保险措施：永远不给窗口任务栏按钮，也不创建托盘图标。"""
        try:
            WS_EX_TOOLWINDOW, WS_EX_APPWINDOW = 0x00000080, 0x00040000
            GWL_EXSTYLE = -20
            hwnd = wintypes.HWND(int(self.winId()))
            ex = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ex |= WS_EX_TOOLWINDOW
            ex &= ~WS_EX_APPWINDOW
            _user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)
        except Exception:  # noqa: BLE001 - 失败也无所谓，Qt 的 Tool 标志已生效
            pass

    # ------------------------------------------------------------------
    # 顶置 + 显隐
    # ------------------------------------------------------------------
    def _force_topmost(self) -> None:
        if not self.settings.always_on_top:
            return
        try:
            HWND_TOPMOST, SWP_NOMOVE, SWP_NOSIZE, SWP_SHOWWINDOW = -1, 0x2, 0x1, 0x40
            ctypes.windll.user32.SetWindowPos(
                ctypes.c_void_p(int(self.winId())), ctypes.c_void_p(HWND_TOPMOST),
                0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
        except Exception:  # noqa: BLE001 - 非 Windows / 权限不足时忽略
            pass

    def _clear_native_title(self) -> None:
        """把 Win32 窗口标题清成空串。

        为什么必须走 Win32：Qt 的 setWindowTitle("") 会被忽略并回退成应用名
        （main.py 里 setApplicationName("Audio")），结果原生标题仍是 "Audio"。
        而 Chrome 等的「共享某个窗口」选择器正是靠枚举顶层窗口 + 取标题工作的，
        标题非空就会被列出来，用户一点就等于把悬浮窗共享出去。
        """
        try:
            _user32.SetWindowTextW(wintypes.HWND(int(self.winId())), "")
        except Exception:  # noqa: BLE001 - 非 Windows / 权限不足时忽略
            pass

    def showEvent(self, event):
        super().showEvent(event)
        self._ensure_no_taskbar()
        # setWindowFlag 会重建原生窗口句柄，affinity 会丢，所以每次显示都要重新设置
        self._apply_capture_exclusion()
        self._clear_native_title()
        self._disarm_hover_buttons()
        screen = QGuiApplication.primaryScreen()
        if screen:
            area = screen.availableGeometry()
            if not self.geometry().intersects(area):
                self.move(area.center() - QPoint(self.width() // 2,
                                                 self.height() // 2))
        QTimer.singleShot(120, self._force_topmost)

    def closeEvent(self, event):
        save_credentials(self.settings)
        super().closeEvent(event)

    def _show_by_hotkey(self) -> None:
        self.show()
        self.raise_()
        self._force_topmost()

    def _hide_by_hotkey(self) -> None:
        self.hide()

    # ------------------------------------------------------------------
    # 按钮动作
    # ------------------------------------------------------------------
    def _set_staging(self, staging: bool) -> None:
        """暂存页面时把按钮文案换成「发送 / 取消」。"""
        self.btn_solve.set_text("发送" if staging else "解题")
        self.btn_reset.set_text("取消" if staging else "重置")

    def on_solve(self) -> None:
        if self._busy:
            return
        if self._pages:
            self._send_pages()
        else:
            self._single_solve()

    def _send_pages(self) -> None:
        """把暂存的所有页面一次性发给后端（最多 max_pages 张）＝新会话。"""
        if not self._pages:
            self._set_staging(False)
            return
        pages = list(self._pages)
        self._pages.clear()
        self._set_staging(False)
        info = f"{len(pages)} 页"
        self._start_request(pages, info)

    def _single_solve(self) -> None:
        """没有暂存页面：清掉上一轮缓存，抓当前屏幕直接发（抓屏在子线程做）。"""
        for capture in self._pages:
            capture.cleanup()
        self._pages.clear()
        self._set_staging(False)
        self._reason_text = ""
        self._set_status("抓屏中…", force=True)
        self._capture_then(self._on_single_captured)

    def _on_single_captured(self, capture: Capture) -> None:
        self._start_request([capture], f"{capture.width}×{capture.height}")

    def _capture_then(self, callback) -> None:
        """子线程抓屏；浏览器在前台时先把自己藏起来，截完自动回来。"""
        in_browser = (foreground_process_name()
                      in tuple(self.settings.browser_processes))
        was_visible = self.isVisible()
        if in_browser and was_visible:
            self.hide()

        def restore():
            if was_visible:
                self.show()
                self.raise_()

        def on_done(capture):
            restore()
            callback(capture)

        def on_failed(message: str):
            restore()
            self._finish_busy("抓屏失败")
            self.body.set_text(f"抓屏失败：{message}")

        worker = CaptureWorker(self.settings.current_monitor_only)
        worker.signals.done.connect(on_done)
        worker.signals.failed.connect(on_failed)
        QThreadPool.globalInstance().start(worker)

    def on_add_page(self) -> None:
        """加页 = 只抓屏缓存，不发给后端；攒够后再统一发送。"""
        if self._busy:
            return
        if len(self._pages) >= self.settings.max_pages:
            self._set_status(f"已达上限 {self.settings.max_pages} 页",
                             force=True)
            return
        self._set_status("抓屏中…", force=True)
        self._capture_then(self._on_page_captured)

    def _on_page_captured(self, capture: Capture) -> None:
        self._pages.append(capture)
        self._set_staging(True)
        self._set_status(f"已加页 {len(self._pages)}/{self.settings.max_pages}",
                         force=True)

    def on_reset(self) -> None:
        """重置 / 取消 = 开一个全新会话：正文、思维链缓存、已暂存页面全部清掉。"""
        for capture in self._pages:
            capture.cleanup()          # 顺手删掉临时截图文件
        self._pages.clear()
        self.body.set_text("")
        self.body.scroll_to_extreme("top")
        self._reason_text = ""
        self._set_staging(False)
        self._refresh_arrows()
        self._set_status("已重置", force=True)

    def on_copy(self) -> None:
        """把正文整段复制到剪贴板——这是答案唯一的出口。

        流式输出中途也能复制当前已出的部分。
        """
        text = self.body.text()
        if not text.strip():
            self._set_status("无内容可复制", force=True)
            return
        QApplication.clipboard().setText(text)
        self._set_status(f"已复制 {len(text)} 字", force=True)

    def on_quit(self) -> None:
        for capture in self._pages:
            capture.cleanup()
        save_credentials(self.settings)
        QApplication.quit()

    # ------------------------------------------------------------------
    # 请求
    # ------------------------------------------------------------------
    def _start_request(self, pages, info: str) -> None:
        self._busy = True
        self._started_at = time.monotonic()
        self.body.set_text("")
        self.body.scroll_to_extreme("top")
        self._reason_text = ""
        self._set_status(f"已抓取（{info}）", force=True)
        for button in self._hover_buttons:
            button.set_active(False)

        worker = SolveWorker([p.base64 for p in pages], self.settings)
        worker.signals.reasoning.connect(self._on_reasoning)
        worker.signals.content.connect(self._on_content)
        worker.signals.finished.connect(self._on_result)
        worker.signals.failed.connect(self._on_error)
        QThreadPool.globalInstance().start(worker)

    # ---- 流式回调 ----
    def _on_reasoning(self, chunk: str) -> None:
        """思维链只收不显示：模型自己推理，界面保持安静。"""
        self._reason_text += chunk

    def _on_content(self, chunk: str) -> None:
        self.body.append_text(chunk)
        self._set_status("输出中")
        # 箭头可见性只需要大致跟上，不每个 token 都重算（原本 25 次/秒），
        # 改成单一可复位的定时器，最多 6 次/秒。
        self._arrow_timer.start()

    def _set_status(self, text: str, force: bool = False) -> None:
        """状态栏节流：流式期间每个 token 都 setText 会白白触发一次重绘。"""
        now = time.monotonic()
        if not force and now - self._status_ts < self.settings.status_throttle_ms / 1000.0:
            self._status_pending = text
            self._status_timer.start()    # 到点补刷
            return
        self._status_ts = now
        self._status_pending = None
        self._status_timer.stop()
        self.status.setText(text)

    def _flush_status(self) -> None:
        """节流窗口到了，把攒下的那条状态补上。"""
        if self._status_pending is not None:
            text, self._status_pending = self._status_pending, None
            self._status_ts = time.monotonic()
            self.status.setText(text)

    def _on_result(self, text: str, reasoning: str) -> None:
        if not text.strip():
            self.body.set_text(reasoning.strip()[-400:] or "（无输出）")
        # 一轮结束：临时截图与思维链缓存都清掉，下一次「解题」自然是全新会话
        for capture in self._pages:
            capture.cleanup()
        self._pages.clear()
        self._set_staging(False)
        self._reason_text = ""
        self._finish_busy(f"完成 · {time.monotonic() - self._started_at:.1f}s")
        self._refresh_arrows()

    def _on_error(self, message: str) -> None:
        self.body.set_text(f"失败：{message}")
        self._finish_busy("出错")

    def _finish_busy(self, status: str) -> None:
        self._busy = False
        self._set_status(status, force=True)   # 收尾状态必须立刻显示，不吃节流
        for button in self._hover_buttons:
            button.set_active(True)

    # ------------------------------------------------------------------
    # 滚动
    # ------------------------------------------------------------------
    def _on_arrow_scroll(self, delta: float) -> None:
        self.body.scroll_by(delta)
        self._refresh_arrows()

    def _refresh_arrows(self) -> None:
        """箭头只在正文可滚动时才出现；同时把边界状态告诉箭头，让它到头就停表。"""
        scrollable = self.body.max_scroll() > 0.5
        self.arrow_up.setVisible(scrollable)
        self.arrow_down.setVisible(scrollable)
        # 规格 4.5：到顶/到底时悬停计时器自动停止，不在边界空转
        scroll = self.body._scroll
        max_scroll = self.body.max_scroll()
        self.arrow_up.notify_position(scroll <= 0.5, False)
        self.arrow_down.notify_position(False, scroll >= max_scroll - 0.5)

    # ------------------------------------------------------------------
    # 原生消息：热键分发
    # ------------------------------------------------------------------
    def nativeEvent(self, event_type, message):
        try:
            msg, wparam = message_fields(message)
        except Exception:  # noqa: BLE001
            return False, 0
        if msg == WM_HOTKEY:
            self.hotkeys.dispatch(int(wparam))
            return True, 0
        return super().nativeEvent(event_type, message)