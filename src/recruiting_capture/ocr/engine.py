"""OCR 层抽象、结果变换与置信度过滤（NEWREADME 6.5.1）。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol, Sequence, runtime_checkable

from ..capture.driver import Frame
from ..errors import OcrError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OcrLine:
    """一行 OCR 结果；box 为裁切图内坐标 [x1, y1, x2, y2]。"""

    text: str
    score: float
    box: tuple[int, int, int, int]

    @property
    def center(self) -> tuple[int, int]:
        return (self.box[0] + self.box[2]) // 2, (self.box[1] + self.box[3]) // 2


@runtime_checkable
class OcrEngine(Protocol):
    calls: int

    def recognize(self, frame: Frame, threshold: float = 0.8) -> list[OcrLine]: ...


def _box_to_ints(box: Any) -> tuple[int, int, int, int]:
    if hasattr(box, "tolist"):
        box = box.tolist()
    values = list(box)
    if len(values) != 4:
        raise OcrError(f"OCR 文本框坐标格式非法：{box!r}")
    return tuple(int(round(float(v))) for v in values)  # type: ignore[return-value]


def sort_ocr_lines(lines: Sequence[OcrLine]) -> list[OcrLine]:
    """按阅读顺序（先上后下、先左后右）排序。"""

    return sorted(lines, key=lambda line: (line.box[1], line.box[0]))


def transform_predict_result(
    predict_result: Any,
    threshold: float = 0.8,
) -> list[OcrLine]:
    """PaddleOCR ``predict()`` 返回列表 → 过滤/变换后的文本行。

    - 单图结果在列表第 0 项（6.5.1）；
    - 置信度低于阈值的结果丢弃；
    - 输出按 (y1, x1) 升序排列。
    """

    if predict_result is None:
        return []
    if isinstance(predict_result, (list, tuple)):
        if not predict_result:
            return []
        item = predict_result[0]
    else:
        item = predict_result

    try:
        texts = item["rec_texts"]
        scores = item["rec_scores"]
        boxes = item["rec_boxes"]
    except (KeyError, TypeError, IndexError) as exc:
        raise OcrError(
            "OCR 结果缺少 rec_texts/rec_scores/rec_boxes 字段，"
            "请确认 PaddleOCR 版本（本项目要求 3.x）"
        ) from exc

    lines: list[OcrLine] = []
    for text, score, box in zip(texts, scores, boxes):
        text = str(text).strip()
        if not text:
            continue
        score_value = float(score)
        if score_value < threshold:
            continue
        lines.append(OcrLine(text=text, score=score_value, box=_box_to_ints(box)))
    result = sort_ocr_lines(lines)
    logger.debug("OCR 结果变换：保留 %d 行（阈值 %.2f）", len(result), threshold)
    return result
