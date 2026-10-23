"""半透明选框渲染与 PNG 编码。"""

from __future__ import annotations

from PIL import Image

from recruiting_capture.imaging import PALETTE, encode_png, render_regions, save_overlay
from recruiting_capture.models.region import Region


def _image() -> Image.Image:
    return Image.new("RGB", (200, 120), (255, 255, 255))


def test_render_regions_keeps_size_and_changes_pixels() -> None:
    image = _image()
    region = Region.from_list("A", [20, 20, 100, 80])
    rendered = render_regions(image, [region])
    assert rendered.size == image.size
    assert rendered.mode == "RGB"
    assert rendered.getpixel((60, 50)) != (255, 255, 255)
    assert rendered.getpixel((5, 5)) == (255, 255, 255)


def test_render_multiple_regions_uses_palette() -> None:
    regions = [
        Region.from_list("A", [0, 0, 30, 30]),
        Region.from_list("B", [40, 0, 70, 30]),
        Region.from_list("C", [80, 0, 110, 30]),
    ]
    rendered = render_regions(_image(), regions)
    assert rendered.size == (200, 120)
    assert len(PALETTE) >= 3


def test_render_clips_out_of_bounds() -> None:
    region = Region.from_list("A", [150, 80, 400, 300])
    rendered = render_regions(_image(), [region])
    assert rendered.size == (200, 120)
    assert rendered.getpixel((190, 110)) != (255, 255, 255)


def test_clip_upper_left_out_of_bounds() -> None:
    region = Region.from_list("A", [0, 0, 200, 120])
    rendered = render_regions(_image(), [region])
    assert rendered.size == (200, 120)


def test_encode_png() -> None:
    payload = encode_png(_image())
    assert payload.startswith(b"\x89PNG")


def test_save_overlay(tmp_path) -> None:
    target = save_overlay(
        _image(), [Region.from_list("A", [10, 10, 50, 50])], tmp_path / "overlay.png"
    )
    assert target.is_file()
    assert Image.open(target).size == (200, 120)
