"""OCR 层导出。"""

from .engine import OcrEngine, OcrLine, sort_ocr_lines, transform_predict_result
from .mock_engine import MockOcrEngine

__all__ = [
    "OcrEngine",
    "OcrLine",
    "MockOcrEngine",
    "sort_ocr_lines",
    "transform_predict_result",
]


def __getattr__(name: str):
    if name == "PaddleOcrEngine":
        from .paddle_engine import PaddleOcrEngine

        return PaddleOcrEngine
    raise AttributeError(name)
