"""T2：OCR 结果变换与置信度过滤（回放真实 PaddleOCR 样本）。"""

from __future__ import annotations

import json

import pytest

from recruiting_capture.errors import OcrError
from recruiting_capture.ocr.engine import transform_predict_result


def _load_item(samples_dir):
    payload = json.loads((samples_dir / "ocr_sample_1080p.json").read_text(encoding="utf-8"))
    return payload["item"]


def test_transform_filters_low_scores(samples_dir) -> None:
    item = _load_item(samples_dir)
    scores = item["rec_scores"]
    expected = sum(
        1 for text, score in zip(item["rec_texts"], scores) if score >= 0.8 and str(text).strip()
    )
    lines = transform_predict_result([item], threshold=0.8)
    assert len(lines) == expected
    assert all(line.score >= 0.8 for line in lines)
    assert all(isinstance(line.box, tuple) and len(line.box) == 4 for line in lines)
    assert all(value >= 0 for line in lines for value in line.box)


def test_transform_sorts_reading_order(samples_dir) -> None:
    item = _load_item(samples_dir)
    lines = transform_predict_result(item, threshold=0.0)
    keys = [(line.box[1], line.box[0]) for line in lines]
    assert keys == sorted(keys)


def test_transform_threshold_extremes(samples_dir) -> None:
    item = _load_item(samples_dir)
    assert transform_predict_result([item], threshold=1.1) == []
    all_lines = transform_predict_result([item], threshold=0.0)
    high_lines = transform_predict_result([item], threshold=0.8)
    assert len(all_lines) >= len(high_lines)


def test_transform_accepts_single_dict(samples_dir) -> None:
    item = _load_item(samples_dir)
    assert transform_predict_result(item, 0.8) == transform_predict_result([item], 0.8)


def test_transform_missing_fields_raises() -> None:
    with pytest.raises(OcrError):
        transform_predict_result([{"rec_texts": ["a"]}], 0.8)


def test_transform_empty_result() -> None:
    assert transform_predict_result([], 0.8) == []
    assert transform_predict_result(None, 0.8) == []
