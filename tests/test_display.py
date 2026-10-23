"""显示层：等比缩放/压缩与“显示坐标 → 原图坐标”换算（修复截图展示与框选偏移）。"""

from __future__ import annotations

import pytest
from PIL import Image, ImageDraw

from recruiting_capture.display import (
    DisplayMapping,
    compute_display_size,
    describe_mapping,
    encode_display_bytes,
    image_size,
    make_display_copy,
    mapping_from_dict,
    mapping_to_dict,
)


def _sample_image(path, size=(1920, 1080)):
    image = Image.new("RGB", size, (250, 250, 250))
    draw = ImageDraw.Draw(image)
    # 四角与边中点标记，用来目测/验证缩放后是否被裁切
    draw.rectangle([0, 0, 40, 40], fill=(255, 0, 0))
    draw.rectangle([size[0] - 40, size[1] - 40, size[0] - 1, size[1] - 1], fill=(0, 0, 255))
    draw.line([0, size[1] // 2, size[0], size[1] // 2], fill=(0, 160, 0), width=3)
    image.save(path)
    return path


def test_compute_display_size_limits_without_distortion() -> None:
    width, height, mapping = compute_display_size(1920, 1080, 1280, 800)
    assert (width, height) == (1280, 720)
    assert mapping.original_width == 1920 and mapping.original_height == 1080
    # 原始宽高比 16:9 必须保持
    assert abs(width / height - 1920 / 1080) < 0.01


def test_compute_display_size_never_upscales() -> None:
    width, height, mapping = compute_display_size(800, 600, 1280, 800)
    assert (width, height) == (800, 600)
    assert mapping.is_identity


def test_compute_display_size_respects_height_limit() -> None:
    width, height, _ = compute_display_size(1000, 2000, 1280, 800)
    assert height == 800
    assert abs(width / height - 0.5) < 0.01


def test_coordinate_mapping_roundtrip_corners() -> None:
    mapping = compute_display_size(1920, 1080, 1280, 800)[2]
    assert mapping.to_original_point(0, 0) == (0, 0)
    assert mapping.to_original_point(mapping.display_width, mapping.display_height) == (1920, 1080)
    assert mapping.to_original_point(mapping.display_width // 2, mapping.display_height // 2) == (
        960,
        540,
    )


def test_coordinate_mapping_known_region() -> None:
    """示例计划中区域 A 的原图坐标 [212,300,620,930] 经显示缩放后应能精确还原。"""

    mapping = compute_display_size(1920, 1080, 1280, 800)[2]
    dx1, dy1 = mapping.to_display_point(212, 300)
    dx2, dy2 = mapping.to_display_point(620, 930)
    restored = mapping.to_original_rect(dx1, dy1, dx2, dy2)
    assert restored == (212, 300, 620, 930)


def test_coordinate_mapping_clamps_to_image() -> None:
    mapping = DisplayMapping(1920, 1080, 1280, 720)
    assert mapping.to_original_point(-50, -20) == (0, 0)
    assert mapping.to_original_point(5000, 5000) == (1920, 1080)


def test_make_display_copy_downscales_and_keeps_ratio(tmp_path) -> None:
    source = _sample_image(tmp_path / "source.png")
    dest = tmp_path / "display.png"
    mapping = make_display_copy(source, dest, max_width=1280, max_height=800)
    assert dest.is_file()
    assert image_size(dest) == (mapping.display_width, mapping.display_height) == (1280, 720)
    assert image_size(source) == (1920, 1080), "原图不得被修改"
    with Image.open(dest) as image:
        # 右下角蓝色标记在缩放后仍然存在（说明没有被裁切）
        assert image.getpixel((image.width - 5, image.height - 5))[2] > 100


def test_make_display_copy_uses_jpeg_when_no_suffix_given(tmp_path) -> None:
    source = _sample_image(tmp_path / "source.png")
    dest = tmp_path / "first_screen_display"  # 不写扩展名：由压缩策略决定格式
    make_display_copy(source, dest, max_width=800, max_height=450)
    assert (tmp_path / "first_screen_display.jpg").is_file()


def test_make_display_copy_keeps_explicit_suffix(tmp_path) -> None:
    source = _sample_image(tmp_path / "source.png")
    dest = tmp_path / "display.png"
    make_display_copy(source, dest, max_width=800, max_height=450)
    assert dest.is_file() and dest.stat().st_size > 0
    assert not (tmp_path / "display.jpg").exists()


def test_encode_display_bytes() -> None:
    image = Image.new("RGB", (40, 20), (10, 20, 30))
    payload, mime = encode_display_bytes(image, jpeg_quality=80, force_jpeg=True)
    assert mime == "image/jpeg" and payload.startswith(b"\xff\xd8")
    payload_png, mime_png = encode_display_bytes(image)
    assert mime_png == "image/png" and payload_png.startswith(b"\x89PNG")


def test_mapping_serialization_roundtrip() -> None:
    mapping = compute_display_size(2560, 1440, 1280, 800)[2]
    restored = mapping_from_dict(mapping_to_dict(mapping))
    assert restored == mapping


def test_describe_mapping_mentions_scale() -> None:
    mapping = compute_display_size(1920, 1080, 1280, 800)[2]
    text = describe_mapping(mapping)
    assert "1920×1080" in text and "缩放" in text


def test_invalid_inputs() -> None:
    with pytest.raises(ValueError):
        compute_display_size(0, 100, 1280, 800)
    with pytest.raises(ValueError):
        compute_display_size(100, 100, 0, 800)
    with pytest.raises(ValueError):
        DisplayMapping(0, 100, 100, 100)
