"""宿主进程/窗口探测：为“最小化前端窗口”提供安全的目标定位。

设计约束（修复“截首屏时把命令行窗口打开/前置”的问题）：
- 只做**只读**探测：枚举窗口、读取标题、判断是否最小化；
- **绝不回退**到“当前前台窗口”或任意窗口；
- 所有 ctypes 调用都通过一个可注入的绑定：Windows 下使用 ``_Win32Api``，
  非 Windows 或测试环境可用 ``_NullApi``；不在模块导入时加载任何系统 DLL。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Protocol

logger = logging.getLogger(__name__)

SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
SW_MINIMIZE = 6
SW_RESTORE = 9

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    pid: int | None = None
    is_minimized: bool = False
    has_owner: bool = False
    is_tool_window: bool = False


class WindowBackend(Protocol):
    """窗口操作后端；真实实现见 :class:`Win32Backend`。"""

    def enumerate_windows(self) -> list[WindowInfo]: ...

    def top_level_root(self, hwnd: int) -> int: ...

    def minimize(self, hwnd: int) -> bool: ...

    def restore_without_activation(self, hwnd: int) -> bool: ...

    def is_minimized(self, hwnd: int) -> bool: ...

    def title(self, hwnd: int) -> str: ...


class _NullApi:
    """非 Windows 平台的空实现。"""

    def EnumWindows(self, callback, lparam) -> bool:  # noqa: N802 - 对齐 Win32 命名
        callback = None
        lparam = None
        return True

    def __getattr__(self, name: str):
        raise AttributeError(name)


def _is_windows() -> bool:
    return os.name == "nt"


def _build_win32_api():
    """惰性构建 ctypes 窗口 API；失败返回 ``_NullApi``。"""

    if not _is_windows():
        return _NullApi()
    try:
        import ctypes
        from ctypes import wintypes

        # ctypes.windll 会按需加载 user32.dll/kernel32.dll（已随系统常驻）
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        return _Win32Api(user32=user32, kernel32=kernel32, wintypes=wintypes, ctypes=ctypes)
    except Exception as exc:  # pragma: no cover - 依赖具体 Windows 环境
        logger.warning("Windows 窗口 API 不可用（%s），最小化功能将跳过", exc)
        return _NullApi()


class _Win32Api:
    def __init__(self, *, user32, kernel32, wintypes, ctypes) -> None:
        self.user32 = user32
        self.kernel32 = kernel32
        self.wintypes = wintypes
        self.ctypes = ctypes

    def EnumWindows(self, callback, lparam) -> bool:  # noqa: N802
        proc = self.ctypes.WINFUNCTYPE(
            self.ctypes.c_bool, self.wintypes.HWND, self.wintypes.LPARAM
        )(callback)
        self.user32.EnumWindows(proc, lparam)
        return True


def _text_of(api, hwnd: int) -> str:
    user32 = getattr(api, "user32", None)
    if user32 is None:
        return ""
    try:
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        ctypes = getattr(api, "ctypes")
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value.strip()
    except Exception:  # pragma: no cover
        return ""


def _pid_of(api, hwnd: int) -> int | None:
    user32 = getattr(api, "user32", None)
    if user32 is None:
        return None
    try:
        ctypes = getattr(api, "ctypes")
        pid = ctypes.c_ulong(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value) or None
    except Exception:  # pragma: no cover
        return None


def _has_owner(api, hwnd: int) -> bool:
    user32 = getattr(api, "user32", None)
    if user32 is None:
        return False
    try:
        return bool(user32.GetWindow(hwnd, 4))  # GW_OWNER = 4
    except Exception:  # pragma: no cover
        return False


def _is_tool_window(api, hwnd: int) -> bool:
    user32 = getattr(api, "user32", None)
    if user32 is None:
        return False
    try:
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        return bool(style & WS_EX_TOOLWINDOW) and not (style & WS_EX_APPWINDOW)
    except Exception:  # pragma: no cover
        return False


def _is_minimized(api, hwnd: int) -> bool:
    user32 = getattr(api, "user32", None)
    if user32 is None:
        return False
    try:
        return bool(user32.IsIconic(hwnd))
    except Exception:  # pragma: no cover
        return False


def _is_visible(api, hwnd: int) -> bool:
    user32 = getattr(api, "user32", None)
    if user32 is None:
        return False
    try:
        return bool(user32.IsWindowVisible(hwnd))
    except Exception:  # pragma: no cover
        return False


def list_windows(api=None) -> list[WindowInfo]:
    """枚举可见的顶层窗口（只读）。"""

    api = api or _build_win32_api()
    results: list[WindowInfo] = []

    def _callback(hwnd, _lparam):
        try:
            if not _is_visible(api, hwnd):
                return True
            title = _text_of(api, hwnd)
            if not title:
                return True
            results.append(
                WindowInfo(
                    hwnd=int(hwnd),
                    title=title,
                    pid=_pid_of(api, hwnd),
                    is_minimized=_is_minimized(api, hwnd),
                    has_owner=_has_owner(api, hwnd),
                    is_tool_window=_is_tool_window(api, hwnd),
                )
            )
        except Exception:  # pragma: no cover - 单窗口读取失败不影响枚举
            pass
        return True

    try:
        api.EnumWindows(_callback, 0)
    except Exception as exc:  # pragma: no cover
        logger.warning("枚举窗口失败：%s", exc)
    return results


def top_level_root(hwnd: int, api=None) -> int:
    """返回窗口所属的顶层祖先（浏览器子窗口 → 浏览器窗口）。"""

    api = api or _build_win32_api()
    user32 = getattr(api, "user32", None)
    if user32 is None or not hwnd:
        return int(hwnd or 0)
    try:
        GA_ROOT = 2
        root = user32.GetAncestor(hwnd, GA_ROOT)
        return int(root or hwnd)
    except Exception:  # pragma: no cover
        return int(hwnd)


def is_minimized(hwnd: int, api=None) -> bool:
    api = api or _build_win32_api()
    return _is_minimized(api, hwnd)


def window_title(hwnd: int, api=None) -> str:
    api = api or _build_win32_api()
    return _text_of(api, hwnd)


# ---------------------------------------------------------------------- #
# 进程探测：用于只最小化“本 Streamlit 服务所在的浏览器窗口”
# ---------------------------------------------------------------------- #
def _process_table() -> dict[int, int | None]:
    """返回 {pid: parent_pid}；Windows 用 kernel32.Toolhelp32，其他平台用 /proc。"""

    table: dict[int, int | None] = {}
    if _is_windows():
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.windll.kernel32

            class PROCESSENTRY32W(ctypes.Structure):
                _fields_ = [
                    ("dwSize", wintypes.DWORD),
                    ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                    ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260),
                ]

            snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
            if snapshot and snapshot != -1:
                entry = PROCESSENTRY32W()
                entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
                ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
                while ok:
                    table[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
                    ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
                kernel32.CloseHandle(snapshot)
        except Exception as exc:  # pragma: no cover
            logger.debug("读取进程表失败：%s", exc)
        return table

    proc = Path("/proc")
    if proc.is_dir():
        for item in proc.iterdir():
            if not item.name.isdigit():
                continue
            try:
                stat = (item / "stat").read_text(encoding="utf-8")
                ppid = int(stat.rsplit(")", 1)[1].split()[1])
                table[int(item.name)] = ppid
            except Exception:  # pragma: no cover
                continue
    return table


def ancestor_pids(pid: int | None, max_depth: int = 6, table=None) -> list[int]:
    """返回 pid 及其全部祖先（近 → 远）。"""

    if not pid:
        return []
    table = table if table is not None else _process_table()
    result = [int(pid)]
    current = int(pid)
    for _ in range(max_depth):
        parent = table.get(current)
        if not parent or parent in result:
            break
        result.append(int(parent))
        current = int(parent)
    return result


def browser_like(title: str) -> bool:
    """标题末尾带浏览器标识，说明该窗口属于浏览器（如 “xx - Google Chrome”）。"""

    lowered = (title or "").lower().strip()
    suffixes = (
        "google chrome",
        "microsoft edge",
        "mozilla firefox",
        "firefox",
        "brave",
        "opera",
        "vivaldi",
        "chromium",
        "360se",
        "360chrome",
        "qq浏览器",
    )
    return any(lowered.endswith(suffix) for suffix in suffixes)


def streamlit_window_candidates(
    windows: Iterable[WindowInfo],
    pid_ancestors: Iterable[int],
    title_keyword: str,
    page_keyword: str | None = None,
    node_title: str = "",
) -> list[WindowInfo]:
    """从窗口列表中挑出与“窗口标题关键字”匹配的前端窗口候选（按优先级排序）。

    参数语义严格区分：
    - ``title_keyword``：用户输入的**窗口标题关键字**，是主匹配条件（必须命中标题）；
    - ``node_title``：Streamlit 节点标题，只用于排序加分，**绝不作为过滤条件**；
    - ``page_keyword``：URL ``?page=`` 路由值，只在标题已命中关键字时用于精确选择。

    返回列表的排序依据（同级保持原顺序）：
    1. 标题命中 ``page_keyword``（若提供）优先；
    2. 进程属于本服务祖先优先；
    3. 标题含节点标题优先；
    4. 标题像浏览器窗口优先。

    过滤掉系统工具窗口与所有者窗口；空关键字或没有命中时返回空列表
    （绝不回退到前台窗口，也绝不用节点标题兜底）。
    """

    ancestors = {int(item) for item in pid_ancestors}
    keyword = (title_keyword or "").strip().lower()
    page = (page_keyword or "").strip().lower()
    node = (node_title or "").strip().lower()

    if not keyword:
        return []

    pool = [
        window
        for window in windows
        if not window.is_tool_window
        and not window.has_owner
        and keyword in (window.title or "").lower()
    ]
    if not pool:
        return []

    def sort_key(window: WindowInfo) -> tuple[int, int, int, int]:
        title = (window.title or "").lower()
        return (
            0 if (page and page in title) else 1,
            0 if window.pid in ancestors else 1,
            0 if (node and node in title) else 1,
            0 if browser_like(window.title) else 1,
        )

    return sorted(pool, key=sort_key)


def resolve_target_window(
    title_keyword: str = "",
    page_keyword: str | None = None,
    pid: int | None = None,
    api=None,
    windows: Iterable[WindowInfo] | None = None,
    ancestors: Iterable[int] | None = None,
    node_title: str = "",
) -> WindowInfo | None:
    """定位待最小化的窗口；找不到返回 ``None``（调用方不得回退到前台窗口）。

    ``title_keyword`` 是用户填写的窗口标题关键字（主匹配条件）；
    ``node_title`` 是 Streamlit 节点标题，仅作附加条件。

    ``api`` 为测试注入的绑定：``None`` 表示使用真实 Win32 API；传入对象时
    只有提供了 ``user32`` 属性才会用它做顶层祖先解析，否则按句柄原样返回。
    """

    windows = list(windows) if windows is not None else list_windows(api)
    if ancestors is None:
        import os as _os

        ancestors = ancestor_pids(pid if pid is not None else _os.getpid())
    candidates = streamlit_window_candidates(
        windows,
        ancestors,
        title_keyword=title_keyword,
        page_keyword=page_keyword,
        node_title=node_title,
    )
    if not candidates:
        return None
    routed = candidates[0]
    root: int = routed.hwnd
    if hasattr(api, "user32"):
        root = top_level_root(routed.hwnd, api)
    if root and root != routed.hwnd:
        for window in windows:
            if window.hwnd == root:
                return window
        return WindowInfo(hwnd=root, title=routed.title, pid=routed.pid)
    return routed
