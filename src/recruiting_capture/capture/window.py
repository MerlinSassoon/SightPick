"""Windows 窗口最小化/恢复（NEWREADME 5.2 第 6 步）。

修复要点：
- **不再回退到“当前前台窗口”**：定位不到前端窗口时返回 ``None`` 并记录原因，
  由调用方提示改用倒计时模式，而不是去最小化随便一个窗口（曾导致命令行窗口被影响）；
- 只按“本 Streamlit 服务进程 + 标题关键字”定位目标，并最小化其**顶层祖先窗口**
  （浏览器子页面窗口不会被单独最小化）；
- 截图后**后台恢复**（``SW_SHOWNOACTIVATE``，不抢焦点、不切换前台窗口），
  且只在确认窗口确实处于最小化状态时才调用恢复；
- Win32 枚举/标题读取统一在 :mod:`host` 中延迟绑定，不在导入期加载系统库。
"""

from __future__ import annotations

import ctypes
import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from . import host

logger = logging.getLogger(__name__)

SW_MINIMIZE = 6
SW_SHOWNOACTIVATE = 4
SW_RESTORE = 9

__all__ = [
    "WindowTarget",
    "WindowAction",
    "find_frontend_window",
    "minimize_window",
    "restore_window",
    "minimize_frontend",
    "minimized_frontend",
    "list_windows",
    "window_title",
    "foreground_window",
    "SW_MINIMIZE",
    "SW_RESTORE",
]


def _is_windows() -> bool:
    return os.name == "nt"


def _user32():
    """延迟获取 user32（不触发前台窗口变化，也不弹控制台）。"""

    return ctypes.windll.user32


def list_windows() -> list[host.WindowInfo]:
    """枚举可见顶层窗口（只读，供界面排查用）。"""

    return host.list_windows()


def window_title(hwnd: int) -> str:
    return host.window_title(hwnd)


def foreground_window() -> int | None:
    """当前前台窗口（只读诊断用；**绝不作为最小化目标的回退**）。"""

    if not _is_windows():
        return None
    try:
        return int(_user32().GetForegroundWindow())
    except Exception:  # pragma: no cover
        return None


@dataclass(frozen=True)
class WindowTarget:
    hwnd: int
    title: str

    def describe(self) -> str:
        return f"{self.title!r}（hwnd={self.hwnd}）"


@dataclass
class WindowAction:
    """一次“最小化 → 截图 → 恢复”的窗口操作结果。"""

    target: WindowTarget | None = None
    minimized: bool = False
    restored: bool = False
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.minimized

    def describe(self) -> str:
        if self.ok and self.target is not None:
            text = f"已最小化前端窗口 {self.target.describe()}"
            if self.restored:
                text += "，截图后已在后台恢复（未抢焦点）"
            return text
        return self.reason or "未执行窗口最小化"


def find_frontend_window(
    title_keyword: str,
    *,
    page_keyword: str | None = None,
    node_title: str = "Streamlit",
) -> WindowTarget | None:
    """定位前端窗口；找不到返回 None（调用方不得自行猜测其它窗口）。

    ``title_keyword``：用户输入的**窗口标题关键字**，是主匹配条件；
    ``node_title``：Streamlit 节点标题，仅在已命中关键字的窗口里做精细选择。
    """

    if not _is_windows():
        return None
    keyword = (title_keyword or "").strip()
    if not keyword:
        # 空关键字会让节点标题变成事实上的匹配条件，等同于用户输入无效
        logger.info("未提供窗口标题关键字，跳过前端窗口定位")
        return None
    info = host.resolve_target_window(
        title_keyword=keyword,
        page_keyword=page_keyword,
        pid=os.getpid(),
        node_title=node_title,
    )
    if info is None:
        return None
    return WindowTarget(hwnd=info.hwnd, title=info.title)


def minimize_window(hwnd: int | None) -> bool:
    """最小化指定窗口，并校验其确实进入最小化状态。"""

    if not hwnd or not _is_windows():
        return False
    try:
        _user32().ShowWindow(int(hwnd), SW_MINIMIZE)
    except Exception as exc:  # pragma: no cover
        logger.warning("最小化窗口失败：%s", exc)
        return False
    minimized = host.is_minimized(int(hwnd))
    if minimized:
        logger.info("已最小化窗口：%s（hwnd=%s）", window_title(int(hwnd)), hwnd)
    else:
        logger.warning("调用最小化后窗口仍未最小化：hwnd=%s（窗口可能已关闭）", hwnd)
    return minimized


def restore_window(hwnd: int | None, *, activate: bool = False) -> bool:
    """恢复窗口。默认后台恢复：不激活、不改变前台窗口顺序。"""

    if not hwnd or not _is_windows():
        return False
    if not host.is_minimized(int(hwnd)):
        # 用户自己已经恢复或无最小化状态，不做任何操作
        return False
    try:
        user32 = _user32()
        if activate:
            user32.ShowWindow(int(hwnd), SW_RESTORE)
            user32.SetForegroundWindow(int(hwnd))
        else:
            user32.ShowWindow(int(hwnd), SW_SHOWNOACTIVATE)
        logger.info("已恢复窗口（%s）：hwnd=%s", "前台" if activate else "后台", hwnd)
        return True
    except Exception as exc:  # pragma: no cover
        logger.warning("恢复窗口失败：%s", exc)
        return False


def minimize_frontend(
    title_substring: str,
    *,
    page_keyword: str | None = None,
    node_title: str = "Streamlit",
) -> WindowTarget | None:
    """最小化前端窗口；找不到目标时返回 None 并记录原因（**不做任何回退**）。"""

    if not _is_windows():
        logger.warning("当前系统不是 Windows，跳过前端窗口自动最小化")
        return None
    target = find_frontend_window(
        title_substring, page_keyword=page_keyword, node_title=node_title
    )
    if target is None:
        logger.info(
            "未找到标题包含 %r 的前端窗口；不会最小化任何其它窗口（包括命令行/终端）",
            title_substring,
        )
        return None
    if minimize_window(target.hwnd):
        return target
    return None


@contextmanager
def minimized_frontend(
    title_substring: str,
    *,
    delay: float = 0.6,
    restore: bool = True,
    page_keyword: str | None = None,
    node_title: str = "Streamlit",
) -> Iterator[WindowTarget | None]:
    """截图场景：最小化前端 → 等待画面稳定 → 截图 → 后台恢复。"""

    import time

    target = minimize_frontend(
        title_substring, page_keyword=page_keyword, node_title=node_title
    )
    if target:
        time.sleep(max(0.0, delay))
    try:
        yield target
    finally:
        if restore and target:
            restore_window(target.hwnd)
