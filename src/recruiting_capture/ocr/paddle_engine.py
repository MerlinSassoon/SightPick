"""PaddleOCR 真实实现（NEWREADME 6.4 初始化与 6.5.1）。"""

from __future__ import annotations

import logging

from ..capture.driver import Frame
from ..errors import OcrError
from .engine import OcrLine, transform_predict_result

logger = logging.getLogger(__name__)


class PaddleOcrEngine:
    """封装 ``PaddleOCR(use_textline_orientation=True, lang="ch")``。"""

    def __init__(self, *, lang: str = "ch", use_textline_orientation: bool = True) -> None:
        try:
            from paddleocr import PaddleOCR  # 延迟导入：mock 模式无需安装/加载模型
        except Exception as exc:  # pragma: no cover - 取决于环境
            raise OcrError(
                "无法导入 PaddleOCR，请先执行 `pip install -r requirements.txt`"
            ) from exc
        try:
            self._ocr = PaddleOCR(
                use_textline_orientation=use_textline_orientation, lang=lang
            )
        except Exception as exc:  # pragma: no cover
            raise OcrError(f"PaddleOCR 初始化失败：{exc}") from exc
        self.calls = 0
        self._lang = lang
        logger.info("PaddleOCR 已初始化（lang=%s）", lang)

    def recognize(self, frame: Frame, threshold: float = 0.8) -> list[OcrLine]:
        self.calls += 1
        try:
            result = self._ocr.predict(frame.image)
        except Exception as exc:  # pragma: no cover - 依赖模型/硬件
            raise OcrError(f"PaddleOCR 识别失败（区域 {frame.label or '?'}）：{exc}") from exc
        return transform_predict_result(result, threshold)
