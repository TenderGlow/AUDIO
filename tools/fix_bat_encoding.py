"""把项目里的 .bat 修正成 cmd.exe 能正确执行的形式。

两个坑，缺一个都会让批处理直接报错：

1. **换行符必须是 CRLF**
   cmd.exe 按 CRLF 切分命令。用编辑器存成 LF（Unix 风格）时，
   它会把相邻两行粘成一条命令，于是出现
   `'EXE\\""' 不是内部或外部命令`、`'o.' 不是内部或外部命令`
   这种完全看不懂的报错——实际是解析错位，不是命令写错了。

2. **内容编码必须匹配代码页**
   cmd 按**当前 OEM 代码页**解析文件内容，不是按 UTF-8。
   中文 Windows 的代码页是 936(GBK)，所以文件本身也必须是 GBK，
   并且**不能**再用 `chcp 65001`（那是给 UTF-8 文件用的）。

用法：python tools\\fix_bat_encoding.py [--check]
"""
from __future__ import annotations

import glob
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 这些字符 GBK 没有，转码会直接失败；出现就说明文案要改
NOT_IN_GBK = set("→←↑↓✓✗—…·")


def _read_text(raw: bytes) -> tuple[str, str]:
    """按 GBK 优先的顺序解码，返回 (文本, 原编码)。"""
    for enc in ("gbk", "utf-8"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("gbk", errors="replace"), "gbk/replace"


def convert(path: str, dry: bool = False) -> str:
    with open(path, "rb") as fp:
        raw = fp.read()
    text, encoding = _read_text(raw)

    bad = NOT_IN_GBK.intersection(text)
    if bad:
        return f"含有 GBK 无法表示的字符 {sorted(bad)}，需先改文案"

    fixes = []
    # chcp 65001 是给 UTF-8 文件用的；文件转成 GBK 后它反而让控制台显示乱码
    if "chcp 65001" in text:
        text = text.replace("chcp 65001 >nul\n", "").replace("chcp 65001>nul\n", "")
        text = text.replace("chcp 65001 >nul\r\n", "").replace("chcp 65001>nul\r\n", "")
        fixes.append("去掉 chcp 65001")

    # 统一 CRLF —— 这是批处理能不能跑的根本
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
    if normalized != text:
        fixes.append("换行符改为 CRLF")
    text = normalized

    target = text.encode("gbk")
    if raw == target:
        return "已符合要求"
    if dry:
        return f"(--check) 需要：{'、'.join(fixes) or '重新编码'}"
    with open(path, "wb") as fp:
        fp.write(target)
    note = "、".join(fixes) if fixes else f"重新编码（原 {encoding}）"
    return f"已修正：{note}"


def main() -> int:
    dry = "--check" in sys.argv
    changed = 0
    for path in sorted(glob.glob(os.path.join(ROOT, "*.bat"))):
        result = convert(path, dry=dry)
        if result.startswith("已修正"):
            changed += 1
        print(f"{os.path.basename(path):<24} {result}")
    print(f"\n共 {changed} 个文件需要修正" + ("（dry-run，未写入）" if dry else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())