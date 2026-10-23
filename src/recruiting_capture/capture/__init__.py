"""屏幕驱动层导出。"""

from .driver import Frame, ScreenDriver
from .mock_driver import MockScreenDriver

__all__ = ["Frame", "ScreenDriver", "MockScreenDriver"]


def __getattr__(name: str):  # 延迟导入 pyautogui，避免 mock 路径依赖真实驱动
    if name == "PyAutoGuiDriver":
        from .pyautogui_driver import PyAutoGuiDriver

        return PyAutoGuiDriver
    raise AttributeError(name)
