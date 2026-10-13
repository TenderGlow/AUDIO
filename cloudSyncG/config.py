"""配置：**全部写死在下面的默认值里**，不再提供设置界面。

运行期只会从磁盘 settings.json 里读取"模型凭据"（api_base / api_key / model 等），
其余外观与交互参数一律使用本文件的写死值，想改就改这里。
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields

from . import secret

APP_NAME = "cloudSyncG"
APP_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
CONFIG_PATH = os.path.join(APP_DIR, "settings.json")


@dataclass
class Settings:
    # ---- 一、悬浮样式（写死）----
    theme: str = "light"              # 悬浮主题：浅色
    bg_opacity: float = 0.0           # 悬浮底色浓度：0（底色全透）
    # 文字描边：同主题色的细描边。
    # 实现是 QPainterPath.addText + strokePath（一次描边），
    # 不是早期那种"8~12 方向偏移复制"——那既慢 8~12 倍，又会把字撑肥糊掉。
    stroke_width: float = 1.2         # 细描边宽度(px)
    stroke_alpha: int = 200           # 描边自身的不透明度
    text_opacity: float = 0.10        # 文字透明度：10%（正文/按钮/状态/小圆圈/箭头统一）
    bold: bool = False                # 笔画不加粗
    font_size: int = 8                # 正文字号（100% = 基准 8pt）
    font_button_size: int = 6         # 底部四个按钮字号（小字）
    font_status_size: int = 6         # 右上角状态字号
    width: int = 268                  # 悬浮宽
    height: int = 219                 # 悬浮高
    always_on_top: bool = True

    # ---- 二、模型与 API（写死，取自用户已填好的配置）----
    llm_mode: str = "api"
    api_base: str = "https://llm-69ai4267tbwdiss6.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    api_key: str = "sk-ws-H.PHDYDYM.oUgC.MEUCIBpRihC4ob8BBH74cf8cBb__yCMj6LyBW4rEHJLUPL1GAiEA7V4Oj93Vx-Cuxl-DHZVTxtyq44NgwwOHF3rdkpb-D48"
    model: str = "qwen3.8-flash"
    timeout_s: int = 116
    stream: bool = True               # 流式输出

    # 本地回环代理：主程序只连 127.0.0.1，上游地址与 API Key 留在代理进程里。
    # 注意这只是"隔离"不是"隐藏"——代理进程自己那条到上游的连接仍然可见。
    use_local_proxy: bool = True
    proxy_host: str = "127.0.0.1"
    proxy_port: int = 8781
    proxy_base: str = "http://127.0.0.1:8781"
    proxy_token: str = "cloudSyncG-local-proxy"

    reasoning_effort: str = "high"    # 思考档位：high（不开则由服务端默认，通常也是 high）
    prompt: str = (
        "你是解题老师。我会按顺序发给你若干页整屏截图（第 1 页、第 2 页…）。\n"
        "请严格按下面格式作答：\n"
        "· 先按「第 N 页 · 第 M 题」标明这道题在哪一页、题号是几；\n"
        "· 然后给出【答案】；\n"
        "· 再写【简析】，最多不超过两句话，讲清关键一步即可，不要展开推导；\n"
        "· 多页多题就按页码、题号顺序依次列出；某页没有可识别的题目就跳过，不要臆造。\n"
        "页码硬性要求：每张图片前面都写清楚了「第 N 页截图：」这个标签，"
        "你必须逐张打开图片、核对这张图前面的页码标签之后再报页码，"
        "严禁按题号顺序或题目难度去推断某一题属于第几页——"
        "实测模型经常把第 2 页的题误标成第 1 页，务必逐图核对。\n"
        "即使只收到一张图，也照样按「第 1 页 · 第 M 题」的格式作答。"
    )

    # ---- 三、交互（写死）----
    solve_delay_ms: int = 1200        # 悬停「解题/发送」1.2 秒
    add_page_delay_ms: int = 900      # 悬停「加页」0.9 秒
    reset_delay_ms: int = 900         # 悬停「重置/取消」0.9 秒
    quit_delay_ms: int = 2000         # 悬停「退出」2.0 秒
    copy_delay_ms: int = 800          # 悬停顶栏「复制」0.8 秒
    max_pages: int = 4                # 「加页」最多 4 张
    marker_delay_ms: int = 2000       # 悬停左上角小圆圈 2 秒进入跟随移动
    marker_stop_ms: int = 800         # 鼠标静止 0.8 秒后停止跟随

    # 状态栏节流：流式输出时状态文字不需要每个 token 都刷新
    status_throttle_ms: int = 200

    # 后台热键：界面上不显示、不做任何说明
    hotkey_show: str = "Ctrl+Alt+0"   # 显示悬浮窗
    hotkey_hide: str = "Ctrl+Alt+1"   # 隐藏悬浮窗
    hotkey_quit: str = "Ctrl+Alt+Q"   # ★ 彻底退出（窗口被藏起来时的救命出口）

    # 防录屏 / 防截屏
    anti_capture: bool = True          # 录屏/截屏程序运行时自动隐藏
    capture_check_ms: int = 700        # 检测间隔
    # 硬保证：WDA_EXCLUDEFROMCAPTURE，把窗口从系统级捕获里排除
    exclude_from_capture: bool = True

    # 网页类程序：这些程序在前台时，抓屏瞬间先把自己藏起来，避免被截进去
    browser_processes: tuple = ("chrome", "msedge", "firefox", "brave", "opera")

    # 抓屏范围：True = 只抓鼠标所在的那块显示器（多屏更清晰）；False = 抓整个虚拟桌面
    current_monitor_only: bool = True

    # 正文上下边缘的滚动箭头
    arrow_size: int = 14            # 与左上角小圆圈同尺寸
    arrow_step_px: int = 10         # 每步滚动像素（50ms 一步 → 约 200px/s）
    arrow_step_ms: int = 50


# 只有模型凭据允许被磁盘 settings.json 覆盖（方便你换 key / model），
# 其余外观、交互、提示词一律使用上面写死的值——设置界面已移除。
_FROM_DISK = {"api_base", "api_key", "model"}


def load_settings(path: str = CONFIG_PATH) -> Settings:
    settings = Settings()
    if not os.path.exists(path):
        return settings
    try:
        with open(path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
    except (json.JSONDecodeError, OSError):
        return settings
    for key in _FROM_DISK:
        if key in data:
            setattr(settings, key, data[key])
    # api_key 落盘时是 DPAPI 密文，这里还原成明文供请求使用
    stored_key = getattr(settings, "api_key", "")
    if stored_key.startswith(secret.PREFIX):
        plain = secret.unprotect(stored_key)
        if plain is None:
            # 解不开（换了账户/换了机器）→ 保留写死的默认值，并报一声
            settings.api_key = Settings().api_key
            print("[cloudSyncG] settings.json 里的 api_key 无法解密"
                  "（DPAPI 绑定当前账户），已回退到 config.py 中的默认值。")
        else:
            settings.api_key = plain
    elif stored_key and secret.available():
        # 历史遗留的明文 key：顺手升级成密文，别一直裸着放
        try:
            save_credentials(settings, path)
            print("[cloudSyncG] 已把 settings.json 里的明文 api_key 升级为 DPAPI 密文。")
        except OSError:
            pass
    return settings


def save_credentials(settings: Settings, path: str = CONFIG_PATH) -> None:
    """只落盘模型凭据，方便你手工换 key；写死的参数不会被动过。

    api_key 一律加密后写入（DPAPI，当前账户才能解），不落明文。
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {key: getattr(settings, key) for key in _FROM_DISK}
    if payload.get("api_key"):
        payload["api_key"] = secret.protect(payload["api_key"])
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)


def all_settings(settings: Settings) -> dict:
    """导出全部字段（排查问题时用）。"""
    return asdict(settings)


__all__ = ["Settings", "load_settings", "save_credentials", "all_settings", "fields"]