"""pyautogui 真实驱动（NEWREADME 6.1）。"""

from __future__ import annotations

import logging
import sys

import numpy as np

from ..errors import AutomationAbortedError, InputInjectionError
from .driver import Frame
from .input_check import ensure_input_injection

logger = logging.getLogger(__name__)


class PyAutoGuiDriver:
    """基于 pyautogui 的屏幕驱动。

    pyautogui 在导入时会探测显示环境，因此延迟到实例化时导入。
    构造时执行一次“鼠标输入注入自检”：Win32 的 ``SetCursorPos`` 在缺少
    注入权限时返回失败而不抛异常，若不提前校验，表现为“日志正常、鼠标不动”。
    """

    def __init__(self, pause: float = 0.1, *, verify_input: bool = True) -> None:
        import pyautogui

        self._pyautogui = pyautogui
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = pause
        logger.info("pyautogui 驱动已初始化（PAUSE=%.2f, FAILSAFE=True, 解释器=%s）", pause, sys.executable)
        if verify_input:
            self.verify_input_injection()

    def verify_input_injection(self) -> None:
        """确认本进程能真正移动鼠标；失败抛 :class:`InputInjectionError`。"""

        try:
            ensure_input_injection()
        except InputInjectionError:
            logger.error("鼠标输入注入自检失败（解释器=%s）", sys.executable)
            raise
        logger.info("鼠标输入注入自检通过")

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
        """滚动鼠标。

        ``pyautogui.scroll`` 不返回成功与否，因而无法在本层判断滚动是否真的生效；
        注入能力由构造时的 :meth:`verify_input_injection` 统一把关。
        """

        try:
            self._pyautogui.scroll(int(amount))
        except self._pyautogui.FailSafeException as exc:  # type: ignore[attr-defined]
            raise AutomationAbortedError("滚动触发急停保护（鼠标位于屏幕左上角）") from exc
