"""窗口关键字 → 窗口定位 → 最小化 的链路测试（真实 Win32 枚举，只读）。"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="仅 Windows 有真实窗口枚举")


def test_find_frontend_window_requires_keyword() -> None:
    from recruiting_capture.capture.window import find_frontend_window

    assert find_frontend_window("") is None
    assert find_frontend_window("   ") is None


def test_register_style_host_call_accepts_keyword_first() -> None:
    """host.streamlit_window_candidates 的第一个关键字参数就是用户输入（旧签名兼容）。"""

    from recruiting_capture.capture.host import WindowInfo, streamlit_window_candidates

    windows = [
        WindowInfo(hwnd=1, title="智能招聘数据抓取与可视化平台 - Google Chrome", pid=1),
        WindowInfo(hwnd=2, title="Windows PowerShell", pid=2),
    ]
    candidates = streamlit_window_candidates(windows, [1], "智能招聘数据抓取与可视化平台")
    assert [item.hwnd for item in candidates] == [1]
    # 第二位置参数仍是 page_keyword，第三个新增参数为 node_title
    assert streamlit_window_candidates(windows, [1], "智能招聘", None, "Streamlit")
