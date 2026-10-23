"""文本处理：去重、停止标志匹配、噪声过滤、标题清洗。"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Iterable, Sequence

_WHITESPACE = re.compile(r"\s+", re.UNICODE)
_SALARY_LINE = re.compile(r"^\d+(?:\.\d+)?\s*-\s*\d+(?:\.\d+)?\s*[Kk](?:[·xX*]\s*\d+\s*薪)?$")
_RECRUITER_LINE = re.compile(r"^[\u4e00-\u9fa5]{1,4}(先生|女士)$")
_ADDRESS_LINE = re.compile(r"^(工作地址|公司地址|地址)[:：].*")
_COMPANY_TAG = re.compile(r"^[\u4e00-\u9fa5A-Za-z0-9]{1,8}·(招聘者|HR|HRBP|资深招聘专家|招聘专家)$")

# 招聘页面中与“岗位描述”无关的界面噪声（R8 / 9.4-6）。
NOISE_EXACT = {
    "职位描述",
    "任职要求",
    "岗位职责",
    "刚刚活跃",
    "去App",
    "与BOSS随时沟通",
    "与老板随时沟通",
    "微信扫码分享",
    "举报",
    "收藏",
    "☆收藏",
    "立即沟通",
    "在线",
    "分享",
    "登录",
    "注册",
    "展开",
    "收起",
    "查看更多",
    "加载中",
}

NOISE_PREFIXES = ("工作地址", "公司地址", "去App", "微信扫码")

_NOISE_PATTERNS = (
    _RECRUITER_LINE,
    _ADDRESS_LINE,
    _COMPANY_TAG,
    re.compile(r"^[☆★·•\s]+$"),
    re.compile(r"^\d{1,2}:\d{2}$"),
    re.compile(r"^\d+人(?:看过|投递|收藏)$"),
    re.compile(r"^(在线|刚刚活跃|今日活跃|本周活跃)$"),
)


def normalize_line(text: str) -> str:
    """去首尾空白、折叠内部空白（含全角空格）。"""

    if not isinstance(text, str):
        return ""
    return _WHITESPACE.sub(" ", text.replace("\u3000", " ")).strip()


def compact(text: str) -> str:
    """删除所有空白，用于停止标志的宽松匹配。"""

    if not isinstance(text, str):
        return ""
    return _WHITESPACE.sub("", text.replace("\u3000", ""))


def is_noise_line(text: str) -> bool:
    line = normalize_line(text)
    if not line:
        return True
    if line in NOISE_EXACT:
        return True
    if line.startswith(NOISE_PREFIXES):
        return True
    return any(pattern.search(line) for pattern in _NOISE_PATTERNS)


def dedupe_lines(lines: Iterable[str]) -> list[str]:
    """规范化后按首次出现顺序去重。"""

    seen: set[str] = set()
    result: list[str] = []
    for raw in lines:
        line = normalize_line(raw)
        if not line:
            continue
        key = compact(line)
        if key in seen:
            continue
        seen.add(key)
        result.append(line)
    return result


def clean_lines(lines: Iterable[str], *, drop_noise: bool = True) -> list[str]:
    result: list[str] = []
    for raw in lines:
        line = normalize_line(raw)
        if not line:
            continue
        if drop_noise and is_noise_line(line):
            continue
        result.append(line)
    return dedupe_lines(result)


def contains_marker(
    texts: Iterable[str],
    markers: Sequence[str],
    mode: str = "substring",
) -> str | None:
    """判断一组 OCR 文本中是否出现滚动停止标志。

    NEWREADME 9.4-3 指出原型的精确匹配是 bug；默认按“包含”语义匹配
    （关键字常与其它文本连写，如“工作地址”与地址同行）。
    """

    normalized_markers = [compact(marker) for marker in markers if compact(marker)]
    if not normalized_markers:
        return None
    for raw in texts:
        haystack = compact(raw)
        if not haystack:
            continue
        for marker, original in zip(normalized_markers, markers):
            if mode == "exact":
                if haystack == marker:
                    return original
            elif marker in haystack:
                return original
    return None


def looks_like_salary(text: str) -> bool:
    return bool(_SALARY_LINE.match(normalize_line(text)))


def clean_title(text: str) -> str:
    """清洗岗位名称：去多余空白、去“职位名称：”等前缀、去尾部省略号。"""

    line = normalize_line(text)
    for prefix in ("职位名称：", "职位名称:", "岗位名称：", "岗位名称:"):
        if line.startswith(prefix):
            line = line[len(prefix) :].strip()
    line = line.rstrip("…. ")
    return line.strip()


def pick_title_candidate(lines: Iterable[str]) -> str:
    """纯规则兜底：从候选行中挑最像岗位标题的一行（最长且非噪声）。"""

    candidates = [
        normalize_line(line)
        for line in lines
        if normalize_line(line)
        and not is_noise_line(line)
        and not looks_like_salary(line)
    ]
    if not candidates:
        return ""
    return max(candidates, key=lambda item: (len(compact(item)), item))


def generate_timestamp() -> str:
    """带本地时区偏移的 ISO 8601 时间戳（7.3.2 captured_at）。"""

    return datetime.now().astimezone().isoformat(timespec="seconds")


def merge_unique(existing: Iterable[str], incoming: Iterable[str]) -> list[str]:
    return dedupe_lines([*existing, *incoming])


def safe_text(value: object, default: str = "") -> str:
    if value is None:
        return default
    return value if isinstance(value, str) else str(value)


def truncate(text: str, limit: int = 120) -> str:
    text = normalize_line(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"
