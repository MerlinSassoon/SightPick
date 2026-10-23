"""pyautogui 真实驱动（NEWREADME 6.1）。"""

from __future__ import annotations

import logging

import numpy as np

from ..errors import AutomationAbortedError
from .driver import Frame

logger = logging.getLogger(__name__)


class PyAutoGuiDriver:
    """基于 pyautogui 的屏幕驱动。

    pyautogui 在导入时会探测显示环境，因此延迟到实例化时导入。
    """

    def __init__(self, pause: float = 0.1) -> None:
        import pyautogui

        self._pyautogui = pyautogui
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = pause
        logger.debug("pyautogui 驱动已初始化（PAUSE=%.2f, FAILSAFE=True）", pause)

    def size(self) -> tuple[int, int]:
        width, height = self._pyautogui.size()
        return int(width), int(height)

    def capture(self, rect: tuple[int, int, int, int], label: str | None = None) -> Frame:
        try:
            image = self._pyautogui.screenshot(region=tuple(int(v) for v in rect))
        except self._pyautogui.FailSafeException as exc:  # type: ignore[attr-defined]
            raise AutomationAbortedError(
                "检测到鼠标被移到屏幕左上角，已触发 pyautogui 急停保护"
            ) from exc
        except Exception as exc:  # pragma: no cover - 依赖具体桌面环境
            raise AutomationAbortedError(f"屏幕截图失败：{exc}") from exc
        array = np.array(image)
        logger.debug("截图完成 region=%s size=%s label=%s", rect, image.size, label)
        return Frame(image=array, region=tuple(int(v) for v in rect), label=label, meta={})

    def move_to(self, x: int, y: int, duration: float = 0.01) -> None:
        try:
            self._pyautogui.moveTo(int(x), int(y), duration=max(0.0, float(duration)))
        except self._pyautogui.FailSafeException as exc:  # type: ignore[attr-defined]
            raise AutomationAbortedError("鼠标移动触发急停保护（鼠标位于屏幕左上角）") from exc

    def click(self, x: int | None = None, y: int | None = None) -> None:
        try:
            if x is None or y is None:
                self._pyautogui.click()
            else:
                self._pyautogui.click(int(x), int(y))
        except self._pyautogui.FailSafeException as exc:  # type: ignore[attr-defined]
            raise AutomationAbortedError("点击触发急停保护（鼠标位于屏幕左上角）") from exc

    def scroll(self, amount: int) -> None:
        try:
            self._pyautogui.scroll(int(amount))
        except self._pyautogui.FailSafeException as exc:  # type: ignore[attr-defined]
            raise AutomationAbortedError("滚动触发急停保护（鼠标位于屏幕左上角）") from exc
