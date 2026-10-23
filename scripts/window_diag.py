#!/usr/bin/env python
"""窗口诊断（只读）：列出可见顶层窗口、计算“自动最小化”会对哪个窗口下手。

用于验证“截首屏只最小化前端窗口、不影响命令行/终端”：

1. 在一个命令行窗口里运行本脚本并保持在前台：
       python scripts/window_diag.py --monitor 15
2. 在另一个窗口里点击前端“截取首屏”（自动最小化模式）。
3. 回看本脚本输出：
   - “定位结果”要么是明确的前端窗口，要么是 None（此时前端不会最小化任何窗口）；
   - 监控期间前台窗口的时间线不会出现“命令行窗口被前置”或其它干扰。

本脚本本身不最小化、不激活任何窗口。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from recruiting_capture.capture import host  # noqa: E402
from recruiting_capture.capture.window import foreground_window, window_title  # noqa: E402


def _print_windows() -> None:
    print("== 可见顶层窗口（只读） ==")
    windows = host.list_windows()
    if not windows:
        print("（未枚举到窗口：可能不是 Windows 或当前会话无桌面）")
    for window in windows:
        title = window.title.encode("unicode_escape").decode("ascii")
        print(
            f"hwnd={window.hwnd:<10} pid={str(window.pid):<8} "
            f"min={str(window.is_minimized):<5} tool={str(window.is_tool_window):<5} title={title}"
        )
    print()


def _print_target(keyword: str) -> None:
    target = host.resolve_target_window(title_keyword=keyword, pid=os.getpid())
    print("== 定位结果 ==")
    if target is None:
        print(
            f"title_keyword={keyword!r} → None：前端不会最小化任何窗口"
            "（这正是修复“截首屏影响命令行窗口”后的安全行为）"
        )
    else:
        print(f"title_keyword={keyword!r} → 将只最小化该窗口：{target.title!r}（hwnd={target.hwnd}）")
    print()


def _monitor(seconds: float) -> None:
    print(f"== 监控前台窗口 {seconds:g} 秒（每 0.25s 采样） ==")
    end = time.time() + seconds
    last = object()
    timeline: list[str] = []
    while time.time() < end:
        hwnd = foreground_window()
        if hwnd != last:
            title = window_title(hwnd) if hwnd else ""
            timeline.append(f"{hwnd} {title.encode('unicode_escape').decode('ascii')}")
            last = hwnd
        time.sleep(0.25)
    for item in timeline:
        print(f"foreground {item}")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description="窗口诊断（只读）")
    parser.add_argument(
        "--title-keyword",
        default=None,
        help="窗口标题关键字（必填；与页面里填写的“前端窗口标题关键字”一致）",
    )
    parser.add_argument("--page-keyword", default=None, help="对应前端 URL 的 ?page= 值")
    parser.add_argument("--node-title", default="Streamlit", help="Streamlit 节点标题（附加条件）")
    parser.add_argument("--monitor", type=float, default=0.0, help="监控前台窗口的秒数")
    args = parser.parse_args()

    _print_windows()
    keyword = (args.title_keyword or "").strip()
    if not keyword:
        print("未提供 --title-keyword：请填写页码里的“前端窗口标题关键字”后重试。")
    else:
        _print_target(keyword)
    if args.page_keyword and keyword:
        target = host.resolve_target_window(
            title_keyword=keyword,
            page_keyword=args.page_keyword,
            node_title=args.node_title,
            pid=os.getpid(),
        )
        print(f"page_keyword={args.page_keyword!r} → {target}")
        print()
    if args.monitor > 0:
        _monitor(args.monitor)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
