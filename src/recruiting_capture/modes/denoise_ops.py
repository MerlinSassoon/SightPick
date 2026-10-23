"""结果去噪的纯函数实现（NEWREADME 6.3 函数八点零）。

同步路径（TranscribeMode）与异步路径（LLMWorker）共用这里的唯一实现，
避免两份逻辑漂移。所有函数不得读取任何实例状态。
"""

from __future__ import annotations

from typing import Any

from ..capture.driver import Frame
from ..errors import LLMError
from ..llm.client import LLMClient
from ..models.config import FORMAT_STR, FORMAT_TUPLE
from ..text_utils import clean_lines, clean_title, dedupe_lines, pick_title_candidate


def rule_based_result(texts: list[str], expected_format: str) -> str | list[str]:
    """R6 降级路径：不调用大模型，仅按规则清理。"""
    cleaned = clean_lines(texts, drop_noise=True)
    if expected_format == FORMAT_STR:
        return clean_title(pick_title_candidate(cleaned))
    return cleaned


def apply_expected_format(
    result: str | list[str] | tuple[str, ...],
    fallback_texts: list[str],
    expected_format: str,
) -> str | list[str] | tuple[str, ...]:
    """把大模型返回值规整到配置声明的格式。"""
    if expected_format == FORMAT_STR:
        if isinstance(result, str):
            title = clean_title(result)
        else:
            title = clean_title(pick_title_candidate([str(item) for item in result]))
        if title:
            return title
        return clean_title(pick_title_candidate(fallback_texts))
    lines = [result] if isinstance(result, str) else [str(item) for item in result]
    lines = dedupe_lines(line for line in lines if line.strip())
    if expected_format == FORMAT_TUPLE:
        return tuple(lines)
    return lines


def denoise_once(
    llm: LLMClient,
    *,
    texts: list[str],
    frame: Frame | None,
    expected_content: str,
    expected_format: str,
    context: dict[str, Any] | None = None,
) -> tuple[Any, list[str]]:
    """同步与异步共用的唯一去噪实现。

    返回 ``(已套用期望格式的结果, 告警列表)``；**永不抛异常**（仅限 LLMError）。
    失败时按 R6 降级为规则清理，并返回降级原因。
    """

    try:
        raw = llm.denoise(
            texts=texts,
            frame=frame,
            expected_content=expected_content,
            expected_format=expected_format,
            context=context,
        )
    except LLMError as exc:
        return (
            rule_based_result(texts, expected_format),
            [f"去噪调用大模型失败，按 R6 降级为规则清理：{exc}"],
        )
    return apply_expected_format(raw, texts, expected_format), []
