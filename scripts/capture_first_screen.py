#!/usr/bin/env python
"""截取首屏：默认先最小化前端窗口，截完后台恢复（NEWREADME 5.2 第 1 步）。

与前端一致支持两种窗口处理方式：
- 默认：自动最小化**前端窗口本身**（绝不回退去最小化其它窗口，包括命令行）；
- ``--countdown N``：倒计时 N 秒后直接截图，不做任何窗口操作。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from recruiting_capture.capture.pyautogui_driver import PyAutoGuiDriver  # noqa: E402
from recruiting_capture.capture.window import minimize_frontend, restore_window  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="截取首屏")
    parser.add_argument("--out", default=str(ROOT / "runtime" / "ui" / "first_screen.png"))
    parser.add_argument(
        "--window-title",
        default=None,
        help="前端窗口标题关键字（必填，除非使用 --no-minimize 或 --countdown）",
    )
    parser.add_argument(
        "--countdown",
        type=float,
        default=0.0,
        help="倒计时秒数：开始前等待，期间请手动切换到目标页面（不做任何窗口操作）",
    )
    args = parser.parse_args()

    delay = max(0.0, float(args.countdown))
    if delay:
        print(f"倒计时 {delay:g} 秒后截图，请切换到目标页面（期间不操作任何窗口）。")
        time.sleep(delay)

    driver = PyAutoGuiDriver()
    width, height = driver.size()
    target = None
    keyword = (args.window_title or "").strip()
    if not args.no_minimize and not delay:
        if not keyword:
            print(
                "未提供 --window-title：为避免误最小化命令行等其它窗口，本次不做最小化。"
                "请填写窗口标题关键字，或使用 --countdown / --no-minimize。",
                file=sys.stderr,
            )
        else:
            target = minimize_frontend(keyword)
            if target is None:
                print(
                    f"提示：未找到标题包含“{keyword}”的前端窗口，已跳过自动最小化"
                    "（不会最小化其它窗口）。",
                    file=sys.stderr,
                )
            else:
                time.sleep(0.8)
    try:
        frame = driver.capture((0, 0, width, height), label="fullscreen")
    finally:
        if target is not None:
            restore_window(target.hwnd)

    from PIL import Image

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(frame.image).save(out)
    print(f"已保存首屏：{out}（{width}x{height}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
