"""鼠标输入注入能力自检（Windows）。

背景
----
``pyautogui`` 在 Windows 上最终调用 ``SetCursorPos`` / ``mouse_event``。
当进程没有输入注入权限（例如经某些 venv 启动器包装、或处于 UIPI 受限环境）时，
这些 Win32 调用会**返回 0 表示失败，但不抛出任何异常**，pyautogui 也不检查返回值。
结果就是：日志显示每一步都执行成功，鼠标却纹丝不动——排查成本极高。

本模块通过“移动光标 → 读回坐标 → 复原”的往返验证，把这类静默失败变成显式异常，
并提供可读的诊断信息（解释器路径、Win32 返回值等）。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
import sys

from ..errors import InputInjectionError

__all__ = ["probe_cursor_control", "ensure_input_injection", "describe_input_environment"]

#: 自检时偏移的像素数；足够看出光标变化，又不会把鼠标甩出屏幕。
PROBE_OFFSET = 7

_ADVICE = (
    "当前解释器无法向桌面注入鼠标输入。请改用系统解释器启动，例如\n"
    "    D:\\python\\python.exe -m streamlit run app\\streamlit_app.py\n"
    "并确认终端与会话均为交互式桌面会话（不要用服务/计划任务方式启动）。"
)


def describe_input_environment() -> str:
    """返回一句话的运行环境描述，便于写进日志定位问题。"""

    return f"解释器={sys.executable}；会话={os.environ.get('SESSIONNAME', '未知')}"


def _get_cursor_pos(user32) -> tuple[int, int] | None:
    point = ctypes.wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(point)):
        return None
    return int(point.x), int(point.y)


def probe_cursor_control(user32=None) -> None:
    """验证 ``SetCursorPos`` 是否真正生效；不生效则抛 :class:`InputInjectionError`。

    只读探测：结束时尽量把光标复原，避免干扰用户。
    """

    if os.name != "nt":  # pragma: no cover - 本项目只在 Windows 上跑真实驱动
        return

    if user32 is None:
        user32 = ctypes.windll.user32

    start = _get_cursor_pos(user32)
    if start is None:
        raise InputInjectionError(
            f"无法读取鼠标位置（GetCursorPos 失败）。{_ADVICE}\n{describe_input_environment()}"
        )

    target = (start[0] + PROBE_OFFSET, start[1])
    result = user32.SetCursorPos(int(target[0]), int(target[1]))
    try:
        moved = _get_cursor_pos(user32)
    finally:
        # 无论成败都尝试复原，避免把用户光标留在偏移位置
        user32.SetCursorPos(int(start[0]), int(start[1]))

    if result == 0 or moved is None or moved == start:
        raise InputInjectionError(
            "鼠标输入注入自检失败：SetCursorPos 未能移动光标。\n"
            f"  起点={start} 目标={target} 实际={moved} SetCursorPos 返回值={result}\n"
            f"{_ADVICE}\n{describe_input_environment()}"
        )


def ensure_input_injection(user32=None) -> None:
    """:func:`probe_cursor_control` 的别名，语义化命名便于调用处阅读。"""

    probe_cursor_control(user32)
