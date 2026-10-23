"""T4：滚动停止条件（修复 NEWREADME 9.4-3 的精确匹配问题）。"""

from __future__ import annotations

from recruiting_capture.text_utils import contains_marker


def test_substring_matches_joined_text() -> None:
    texts = ["工作地址：深圳龙岗区坂田", "任职要求："]
    assert contains_marker(texts, ["工作地址"]) == "工作地址"
    assert contains_marker(texts, ["去App", "工作地址"]) == "工作地址"


def test_substring_with_whitespace_and_fullwidth() -> None:
    assert contains_marker(["企业服务\u3000使用与帮助"], ["企业服务"]) == "企业服务"
    assert contains_marker(["使 用 与 帮 助"], ["使用与帮助"]) == "使用与帮助"


def test_exact_mode_requires_equality() -> None:
    texts = ["工作地址：深圳"]
    assert contains_marker(texts, ["工作地址"], mode="exact") is None
    assert contains_marker(["工作地址"], ["工作地址"], mode="exact") == "工作地址"


def test_empty_markers_never_match() -> None:
    assert contains_marker(["任意文本"], []) is None
    assert contains_marker(["任意文本"], [""]) is None


def test_marker_returns_original_marker() -> None:
    assert contains_marker(["prefix 先生 suffix"], ["先生"]) == "先生"
