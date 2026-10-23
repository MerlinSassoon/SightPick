"""OCR 测试替身：读取 mock 驱动放入 Frame.meta 的文本行。"""

from __future__ import annotations

import logging

from ..capture.driver import Frame
from .engine import OcrLine, sort_ocr_lines

logger = logging.getLogger(__name__)


class MockOcrEngine:
    """从 ``frame.meta["lines"]`` 构造 OCR 结果，并执行同样的阈值过滤。"""

    def __init__(self) -> None:
        self.calls = 0

    def recognize(self, frame: Frame, threshold: float = 0.8) -> list[OcrLine]:
        self.calls += 1
        raw_lines = frame.meta.get("lines") or []
        lines: list[OcrLine] = []
        for item in raw_lines:
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            score = float(item.get("score", 1.0))
            if score < threshold:
                continue
            box = item.get("box") or [0, 0, 0, 0]
            lines.append(
                OcrLine(
                    text=text,
                    score=score,
                    box=(int(box[0]), int(box[1]), int(box[2]), int(box[3])),
                )
            )
        return sort_ocr_lines(lines)
