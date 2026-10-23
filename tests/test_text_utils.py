"""文本处理：去重、去噪、标题选择与时间戳。"""

from __future__ import annotations

import json
from datetime import datetime

from recruiting_capture.text_utils import (
    clean_lines,
    clean_title,
    contains_marker,
    dedupe_lines,
    generate_timestamp,
    is_noise_line,
    looks_like_salary,
    merge_unique,
    normalize_line,
    pick_title_candidate,
)


def test_normalize_line() -> None:
    assert normalize_line("  多个   空白\u3000字符  ") == "多个 空白 字符"


def test_dedupe_lines_preserves_order() -> None:
    assert dedupe_lines(["A", " A ", "B", "A", ""]) == ["A", "B"]


def test_clean_lines_removes_noise() -> None:
    lines = ["职位描述", "1、负责后端开发；", "魏女士", "刚刚活跃", "去App", "工作地址：深圳"]
    assert clean_lines(lines) == ["1、负责后端开发；"]


def test_noise_detection() -> None:
    assert is_noise_line("魏女士")
    assert is_noise_line("外企德科数字·资深招聘专家")
    assert is_noise_line("☆")
    assert is_noise_line("工作地址：深圳龙岗区")
    assert not is_noise_line("本科及以上学历，3 年以上经验；")
    assert not is_noise_line("任职要求：")


def test_salary_detection() -> None:
    assert looks_like_salary("14-28K·14薪")
    assert looks_like_salary("11-22K")
    assert not looks_like_salary("中高级开发工程师（20-40k*14-16薪）")


def test_pick_title_candidate() -> None:
    lines = ["14-28K·14薪", "收藏", "python开发工程师", "Docker"]
    assert pick_title_candidate(lines) == "python开发工程师"


def test_clean_title_prefix_and_suffix() -> None:
    assert clean_title("岗位名称：python开发工程师…") == "python开发工程师"
    assert clean_title("  中高级开发工程师  ") == "中高级开发工程师"


def test_timestamp_is_iso_with_timezone() -> None:
    value = generate_timestamp()
    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo is not None


def test_merge_unique() -> None:
    assert merge_unique(["A", "B"], ["B", "C"]) == ["A", "B", "C"]


def test_dirty_prototype_sample_is_cleanable(samples_dir) -> None:
    payload = json.loads((samples_dir / "prototype_out_dirty.json").read_text(encoding="utf-8"))
    combined = [line for lines in payload.values() for line in lines]
    assert any(is_noise_line(line) for line in combined)
    assert any(line == "去App" for line in combined)
    cleaned = clean_lines(combined)
    assert "去App" not in cleaned
    assert len(cleaned) == len(set(cleaned))


def test_contains_marker_in_clean_lines() -> None:
    assert contains_marker(clean_lines(["工作地址：深圳"]), ["工作地址"]) is None  # 地址行本身是噪声
