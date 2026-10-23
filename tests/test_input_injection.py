"""鼠标输入注入自检。

回归背景：``SetCursorPos`` 在缺少输入注入权限的进程里**返回 0 而不抛异常**，
表现为“日志每一步都成功、鼠标却完全不动”。本文件用伪造的 user32 覆盖
四种情形，确保自检能区分“真的动了”和“静默失败”。
"""

from __future__ import annotations

import pytest

from recruiting_capture.capture.input_check import (
    PROBE_OFFSET,
    describe_input_environment,
    ensure_input_injection,
    probe_cursor_control,
)
from recruiting_capture.errors import InputInjectionError


class _FakeUser32:
    """伪造 user32：按 ``mode`` 模拟不同故障形态。"""

    def __init__(self, mode: str, start: tuple[int, int] = (400, 300)) -> None:
        self.mode = mode
        self.cursor = start
        self.start = start
        self.set_calls: list[tuple[int, int]] = []

    def GetCursorPos(self, ref) -> int:
        if self.mode == "unreadable":
            return 0
        ref._obj.x, ref._obj.y = self.cursor
        return 1

    def SetCursorPos(self, x: int, y: int) -> int:
        self.set_calls.append((x, y))
        if self.mode == "blocked":
            # 关键：返回 0（失败），但光标不动，且不抛异常
            return 0
        if self.mode == "silent":
            # 关键：返回值“成功”，但光标实际没动
            return 1
        self.cursor = (x, y)
        return 1


def test_probe_passes_when_cursor_moves() -> None:
    user32 = _FakeUser32("ok", start=(400, 300))
    probe_cursor_control(user32)
    start = (400, 300)
    assert user32.set_calls[0] == (start[0] + PROBE_OFFSET, start[1])
    # 探测结束后必须复原光标
    assert user32.set_calls[-1] == start


def test_probe_raises_when_system_refuses_injection() -> None:
    """SetCursorPos 返回 0 —— 正是 venv 启动器下的真实故障。"""

    user32 = _FakeUser32("blocked")
    with pytest.raises(InputInjectionError) as info:
        probe_cursor_control(user32)
    message = str(info.value)
    assert "自检失败" in message
    assert "SetCursorPos 返回值=0" in message
    # 必须给出可操作的修复建议与解释器路径
    assert "D:\\python\\python.exe" in message
    assert "解释器=" in message


def test_probe_raises_when_setcursorpos_silently_ignored() -> None:
    """返回值是“成功”但光标没动，同样必须判定失败。"""

    user32 = _FakeUser32("silent")
    with pytest.raises(InputInjectionError) as info:
        probe_cursor_control(user32)
    assert "实际=" in str(info.value)


def test_probe_raises_when_cursor_unreadable() -> None:
    user32 = _FakeUser32("unreadable")
    with pytest.raises(InputInjectionError) as info:
        probe_cursor_control(user32)
    assert "无法读取鼠标位置" in str(info.value)


def test_probe_always_restores_cursor() -> None:
    """即使探测失败，也必须把光标复原，避免干扰用户。"""

    user32 = _FakeUser32("blocked", start=(777, 555))
    with pytest.raises(InputInjectionError):
        probe_cursor_control(user32)
    assert user32.set_calls[-1] == (777, 555)


def test_ensure_is_alias() -> None:
    user32 = _FakeUser32("ok")
    ensure_input_injection(user32)
    assert user32.set_calls


def test_describe_environment_mentions_interpreter() -> None:
    text = describe_input_environment()
    assert "解释器=" in text
