"""确定性 mock 视觉大模型：用于 demo / 单测，不参与生产路径。"""

from __future__ import annotations

import logging
from typing import Any

from ..capture.driver import Frame
from ..text_utils import (
    clean_lines,
    clean_title,
    compact,
    looks_like_salary,
)
from .client import AnchorConfirmation, ClickConfirmation

logger = logging.getLogger(__name__)


class MockLLMClient:
    """按固定规则模拟“确认/去噪”，保证 demo 可重复。"""

    def __init__(self) -> None:
        self.calls = 0

    def denoise(
        self,
        *,
        texts: list[str],
        frame: Frame | None,
        expected_content: str,
        expected_format: str,
        context: dict[str, Any] | None = None,
    ) -> str | list[str] | tuple[str, ...]:
        self.calls += 1
        cleaned = clean_lines(texts, drop_noise=True)
        logger.debug(
            "MockLLM 去噪：输入 %d 行 → 输出 %d 行（期望格式 %s）",
            len(texts),
            len(cleaned),
            expected_format,
        )
        if expected_format == "str":
            # 标题区域（C）通常第一行就是岗位名称；只排除薪资行。
            candidates = [line for line in cleaned if not looks_like_salary(line)]
            return clean_title(candidates[0]) if candidates else ""
        if expected_format == "tuple":
            return tuple(cleaned)
        return cleaned

    def confirm_anchor(
        self,
        *,
        texts: list[str],
        frame: Frame | None,
        anchor: str,
        context: dict[str, Any] | None = None,
    ) -> AnchorConfirmation:
        self.calls += 1
        target = compact(anchor)
        if not target:
            return AnchorConfirmation(exists=True, reason="未配置锚点，直接通过")
        for text in texts:
            if target in compact(text):
                return AnchorConfirmation(exists=True, reason="OCR 文本中直接命中锚点")
        for text in texts:
            if "职位描" in compact(text):
                return AnchorConfirmation(
                    exists=True,
                    corrected_key=text,
                    reason="OCR 错字变体，已修正锚点",
                )
        return AnchorConfirmation(exists=False, reason="OCR 文本与大模型确认均未发现锚点")

    def confirm_click(
        self,
        *,
        texts: list[str],
        frame: Frame | None,
        context: dict[str, Any] | None = None,
    ) -> ClickConfirmation:
        self.calls += 1
        meta = frame.meta if frame is not None else {}
        matched = bool(meta.get("click_is_new", True))
        return ClickConfirmation(
            matched=matched,
            reason="mock 场景：点击命中了新的列表项" if matched else "mock 场景：点击未切换列表项",
        )
