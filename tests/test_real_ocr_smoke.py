"""真实 PaddleOCR 冒烟测试（默认跳过，设置 RUN_REAL_OCR=1 启用）。

只对静态截图做识别，不会移动鼠标或点击任何窗口。
"""

from __future__ import annotations

import os

import numpy as np
import pytest
from PIL import Image

from recruiting_capture.capture.driver import Frame

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    os.environ.get("RUN_REAL_OCR") != "1",
    reason="需要真实 PaddleOCR 模型，设置 RUN_REAL_OCR=1 后运行",
)
def test_paddleocr_reads_sample_screenshot(samples_dir) -> None:
    pytest.importorskip("paddleocr")
    from recruiting_capture.ocr.paddle_engine import PaddleOcrEngine

    image = Image.open(samples_dir / "screenshot_1080p.png").convert("RGB")
    crop = image.crop((660, 330, 1460, 430))
    frame = Frame(image=np.array(crop), region=(660, 330, 800, 100), label="C")
    engine = PaddleOcrEngine()
    lines = engine.recognize(frame, threshold=0.5)
    texts = [line.text for line in lines]
    assert texts, "真实 PaddleOCR 未识别到任何文本"
    assert any("工程师" in text or "开发" in text for text in texts), texts
    assert engine.calls == 1
