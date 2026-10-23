#!/usr/bin/env python
"""NEWREADME 6.5.2 / FR-04：滚动距离实时测试（独立脚本）。

用法示例：
    python scripts/scroll_test.py --x 410 --y 380 --distance -210 --times 5 --interval 0.5
"""

from __future__ import annotations

import argparse
import sys
import time


def main() -> int:
    parser = argparse.ArgumentParser(description="滚动距离实时测试")
    parser.add_argument("--x", type=int, required=True, help="滚动起始坐标 x")
    parser.add_argument("--y", type=int, required=True, help="滚动起始坐标 y")
    parser.add_argument("--distance", type=int, required=True, help="负值向下滚动")
    parser.add_argument("--times", type=int, default=5)
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument("--delay", type=float, default=3.0, help="开始前倒计时")
    args = parser.parse_args()

    try:
        import pyautogui
    except Exception as exc:  # pragma: no cover
        print(f"[失败] 无法导入 pyautogui：{exc}", file=sys.stderr)
        return 1

    pyautogui.FAILSAFE = True
    print(f"{args.delay} 秒后开始滚动测试，请立即切换到目标页面。")
    print("运行期间不要触碰鼠标键盘；鼠标移到屏幕左上角可急停。")
    time.sleep(max(0.0, args.delay))
    pyautogui.moveTo(args.x, args.y, duration=0.2)
    for index in range(1, max(1, args.times) + 1):
        time.sleep(max(0.0, args.interval))
        pyautogui.scroll(args.distance)
        print(f"第 {index}/{args.times} 次滚动：{args.distance}")
    print("测试结束：请检查列表首尾是否有重叠、有无缺漏。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
