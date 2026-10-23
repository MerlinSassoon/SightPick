"""窗口定位策略：用户“窗口标题关键字”是主匹配条件，绝不回退到前台/命令行窗口。"""

from __future__ import annotations

import pytest

from recruiting_capture.capture import host
from recruiting_capture.capture.host import (
    WindowInfo,
    ancestor_pids,
    browser_like,
    resolve_target_window,
    streamlit_window_candidates,
)


def _window(hwnd, title, pid=1000, **kwargs):
    return WindowInfo(hwnd=hwnd, title=title, pid=pid, **kwargs)


WINDOWS = [
    _window(1, "Windows PowerShell", pid=500),
    _window(2, "命令提示符", pid=501),
    _window(3, "智能招聘数据抓取平台 - Streamlit - Google Chrome", pid=1000),
    _window(4, "BOSS直聘 - Google Chrome", pid=1000),
    _window(5, "Streamlit - 记事本", pid=777),
]


def test_user_keyword_is_primary_match() -> None:
    """用户填写真实窗口标题（不含 Streamlit）时，也能定位到前端窗口。"""

    windows = [
        _window(1, "Windows PowerShell", pid=500),
        _window(7, "智能招聘数据抓取与可视化平台 - Google Chrome", pid=777),
    ]
    candidates = streamlit_window_candidates(
        windows, [1000], title_keyword="智能招聘数据抓取与可视化平台"
    )
    assert [item.hwnd for item in candidates] == [7]


def test_node_title_never_widens_beyond_user_keyword() -> None:
    """标题含 Streamlit 但不含用户关键字时，不得被选中（节点标题只是排序加分）。"""

    candidates = streamlit_window_candidates(
        WINDOWS, [1000], title_keyword="BOSS直聘", node_title="Streamlit"
    )
    assert [item.hwnd for item in candidates] == [4]
    # 反例：只写节点标题、用户关键字为空 → 任何窗口都不选
    assert streamlit_window_candidates(
        WINDOWS, [1000], title_keyword="", node_title="Streamlit"
    ) == []


def test_node_title_breaks_ties_within_matches() -> None:
    windows = [
        _window(10, "我的前端 - Google Chrome", pid=999),
        _window(11, "我的前端 - Streamlit - Google Chrome", pid=999),
    ]
    candidates = streamlit_window_candidates(
        windows, [], title_keyword="我的前端", node_title="Streamlit"
    )
    assert [item.hwnd for item in candidates] == [11, 10]


def test_never_picks_terminal_windows() -> None:
    candidates = streamlit_window_candidates(
        WINDOWS, [1000], title_keyword="Streamlit"
    )
    titles = [item.title for item in candidates]
    # 第一个就是会被最小化的窗口；命令行/记事本永远不会排在浏览器前端之前
    assert titles[0] == "智能招聘数据抓取平台 - Streamlit - Google Chrome"
    assert "Windows PowerShell" not in titles and "命令提示符" not in titles


def test_empty_keyword_matches_nothing() -> None:
    """未填写关键字时不得拿节点标题兜底，否则等于用户输入无效。"""

    assert streamlit_window_candidates(WINDOWS, [1000], title_keyword="") == []
    assert streamlit_window_candidates(WINDOWS, [1000], title_keyword="   ") == []
    assert resolve_target_window(
        title_keyword="", windows=WINDOWS, ancestors=[1000], api=object()
    ) is None


def test_keyword_is_cleaned_before_matching() -> None:
    candidates = streamlit_window_candidates(
        WINDOWS, [1000], title_keyword="  streamlit  "
    )
    assert candidates and candidates[0].hwnd == 3


def test_page_route_keyword_refines_within_matches() -> None:
    windows = [
        _window(10, "我的前端 - 调试页 - Microsoft Edge", pid=1000),
        _window(11, "我的前端 - 岗位配置 - Google Chrome", pid=1000),
    ]
    candidates = streamlit_window_candidates(
        windows, [1000], title_keyword="我的前端", page_keyword="岗位配置"
    )
    assert candidates[0].hwnd == 11
    # ?page= 只调整优先级，不会把同样命中关键字的窗口过滤掉
    assert {item.hwnd for item in candidates} == {10, 11}


def test_page_keyword_never_widens_beyond_title_keyword() -> None:
    """?page= 只能缩小范围：标题不含关键字、只含 ?page= 的窗口不得入选。"""

    windows = [
        _window(12, "岗位配置 - 别人的标签 - Google Chrome", pid=999),
        _window(13, "我的前端 - 岗位配置 - Google Chrome", pid=1000),
    ]
    candidates = streamlit_window_candidates(
        windows, [1000], title_keyword="我的前端", page_keyword="岗位配置"
    )
    assert [item.hwnd for item in candidates] == [13]


def test_process_match_preferred_over_browser_match() -> None:
    windows = [
        _window(20, "我的前端 - 别人的标签 - Google Chrome", pid=999),
        _window(21, "我的前端 - Firefox", pid=1000),
    ]
    candidates = streamlit_window_candidates(windows, [1000], title_keyword="我的前端")
    assert [item.hwnd for item in candidates] == [21, 20]


def test_browser_heuristic_when_pid_unknown() -> None:
    windows = [
        _window(30, "我的前端 - 记事本", pid=None),
        _window(31, "我的前端 - Google Chrome", pid=None),
    ]
    candidates = streamlit_window_candidates(windows, [], title_keyword="我的前端")
    assert [item.hwnd for item in candidates] == [31, 30]


def test_no_match_returns_empty_not_foreground() -> None:
    windows = [_window(40, "Windows PowerShell", pid=500), _window(41, "C:\\ Windows 终端", pid=1)]
    assert streamlit_window_candidates(windows, [1000, 999], title_keyword="前端") == []
    assert resolve_target_window(
        title_keyword="前端", windows=windows, ancestors=[1000], api=object()
    ) is None


def test_tool_windows_and_owned_windows_are_filtered() -> None:
    windows = [
        _window(50, "我的前端 托盘", pid=1000, is_tool_window=True),
        _window(51, "我的前端 对话框", pid=1000, has_owner=True),
        _window(52, "我的前端 - Google Chrome", pid=1000),
    ]
    candidates = streamlit_window_candidates(windows, [1000], title_keyword="我的前端")
    assert [item.hwnd for item in candidates] == [52]


def test_browser_like_titles() -> None:
    assert browser_like("x - Google Chrome")
    assert browser_like("x - Microsoft Edge")
    assert not browser_like("Streamlit 配置")


def test_ancestor_pids() -> None:
    table = {100: 50, 50: 10, 10: 1, 1: None}
    assert ancestor_pids(100, table=table) == [100, 50, 10, 1]
    assert ancestor_pids(100, max_depth=2, table=table) == [100, 50, 10]
    assert ancestor_pids(None) == []
    assert ancestor_pids(999, table=table) == [999]


@pytest.mark.skipif(host.os.name != "nt", reason="仅 Windows 有真实窗口枚举")
def test_list_windows_returns_titles() -> None:
    windows = host.list_windows()
    assert isinstance(windows, list)
    assert all(isinstance(item.title, str) for item in windows)


def test_resolve_target_prefers_top_level_root() -> None:
    class FakeApi:
        class user32:
            @staticmethod
            def GetAncestor(hwnd, flag):
                return 100  # 子窗口 → 顶层浏览器窗口

    windows = [
        _window(99, "我的前端 - Chromium 子窗口", pid=1000),
        _window(100, "我的前端 - Google Chrome", pid=1000),
    ]
    target = resolve_target_window(
        title_keyword="我的前端", windows=windows, ancestors=[1000], api=FakeApi()
    )
    assert target is not None and target.hwnd == 100
