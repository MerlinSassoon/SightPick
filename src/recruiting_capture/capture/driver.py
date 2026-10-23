"""驱动层抽象：屏幕截图、鼠标操作。

真实实现见 :mod:`pyautogui_driver`，确定性测试替身见 :mod:`mock_driver`。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import numpy as np


@dataclass
class Frame:
    """一次截图裁切的结果。

    ``image`` 为 RGB 数组；``region`` 是传给 driver 的屏幕矩形；
    ``meta`` 仅供 mock 驱动传回场景信息，真实驱动始终为空 dict。
    """

    image: np.ndarray
    region: tuple[int, int, int, int]
    label: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ScreenDriver(Protocol):
    def size(self) -> tuple[int, int]: ...

    def capture(self, rect: tuple[int, int, int, int], label: str | None = None) -> Frame: ...

    def move_to(self, x: int, y: int, duration: float = 0.01) -> None: ...

    def click(self, x: int | None = None, y: int | None = None) -> None: ...

    def scroll(self, amount: int) -> None: ...
