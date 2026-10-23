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

    # 独立脚本可能不在项目里直接运行，这里内联一份最小自检，避免依赖包导入路径
    ensure = _ensure_cursor_control()
    if ensure is not None:
        print(f"[失败] {ensure}", file=sys.stderr)
        return 1

    pyautogui.FAILSAFE = True
    print(f"{args.delay} 秒后开始滚动测试，请立即切换到目标页面。")
    print("运行期间不要触碰鼠标键盘；鼠标移到屏幕左上角可急停。")
    time.sleep(max(0.0, args.delay))

    target = (args.x, args.y)
    pyautogui.moveTo(target[0], target[1], duration=0.2)
    moved = pyautogui.position()
    if (int(moved[0]), int(moved[1])) != target:
        print(
            f"[失败] 鼠标未能移动到 {target}，实际停在 {tuple(moved)}；滚动测试不会生效，已中止。",
            file=sys.stderr,
        )
        return 1

    for index in range(1, max(1, args.times) + 1):
        time.sleep(max(0.0, args.interval))
        pyautogui.scroll(args.distance)
        print(f"第 {index}/{args.times} 次滚动：{args.distance}")
    print("测试结束：请检查列表首尾是否有重叠、有无缺漏。")
    return 0


def _ensure_cursor_control() -> str | None:
    """检查本进程能否真正移动光标；返回 ``None`` 表示正常，否则返回失败原因。

    Windows 上 ``SetCursorPos`` 在缺少输入注入权限时返回 0 而不抛异常，
    因此必须显式校验，否则会出现“打印成功但鼠标不动”的假象。
    """

    if sys.platform != "win32":
        return None
    import ctypes
    import ctypes.wintypes

    user32 = ctypes.windll.user32
    point = ctypes.wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(point)):
        return f"无法读取鼠标位置。当前解释器：{sys.executable}"
    start = (int(point.x), int(point.y))
    result = user32.SetCursorPos(start[0] + 7, start[1])
    point2 = ctypes.wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(point2))
    moved = (int(point2.x), int(point2.y))
    user32.SetCursorPos(start[0], start[1])
    if result == 0 or moved == start:
        return (
            "鼠标输入注入自检失败（SetCursorPos 未能移动光标），"
            "本进程无法控制鼠标。请改用系统解释器运行，例如 "
            f"D:\\python\\python.exe。当前解释器：{sys.executable}"
        )
    return None


if __name__ == "__main__":
    raise SystemExit(main())
