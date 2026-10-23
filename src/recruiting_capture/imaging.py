"""图像渲染：半透明区域选框（NEWREADME 5.1）与 PNG 编码。"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Mapping, Sequence

from PIL import Image, ImageDraw, ImageFont

from .models.region import Region

# 高对比度调色板（与深色/浅色页面都能区分）
PALETTE = (
    (0, 150, 255),
    (255, 90, 90),
    (0, 190, 120),
    (255, 165, 0),
    (170, 90, 255),
    (0, 200, 220),
    (230, 80, 200),
    (120, 180, 0),
)

FILL_ALPHA = 55
OUTLINE_ALPHA = 230


def _to_rgba(image: Image.Image | str | Path) -> Image.Image:
    if isinstance(image, (str, Path)):
        image = Image.open(image)
    if not isinstance(image, Image.Image):
        raise TypeError(f"render_regions 需要 PIL.Image 或图片路径，收到：{type(image).__name__}")
    return image.convert("RGBA")


def _palette_color(index: int) -> tuple[int, int, int]:
    return PALETTE[index % len(PALETTE)]


def _as_regions(
    regions: Mapping[str, Region] | Sequence[Region],
) -> list[Region]:
    if isinstance(regions, Mapping):
        return list(regions.values())
    return list(regions)


def render_regions(
    image: Image.Image | str | Path,
    regions: Mapping[str, Region] | Sequence[Region],
    *,
    line_width: int = 3,
    show_labels: bool = True,
) -> Image.Image:
    """在原图上叠加半透明矩形和区域编号，返回新的 RGB 图像。"""

    base = _to_rgba(image)
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = ImageFont.load_default()
    width, height = base.size

    for index, region in enumerate(_as_regions(regions)):
        color = _palette_color(index)
        x1 = max(0, min(region.xmin, width - 1))
        y1 = max(0, min(region.ymin, height - 1))
        x2 = max(0, min(region.xmax, width - 1))
        y2 = max(0, min(region.ymax, height - 1))
        draw.rectangle([x1, y1, x2, y2], fill=(*color, FILL_ALPHA))
        draw.rectangle([x1, y1, x2, y2], outline=(*color, OUTLINE_ALPHA), width=line_width)
        if show_labels:
            label = region.name
            try:
                bbox = draw.textbbox((0, 0), label, font=font)
                text_w = bbox[2] - bbox[0]
                text_h = bbox[3] - bbox[1]
            except Exception:  # pragma: no cover - 极老版本 Pillow
                text_w, text_h = 8 * len(label), 11
            pad = 4
            lx, ly = x1 + 2, y1 + 2
            draw.rectangle(
                [lx, ly, lx + text_w + pad * 2, ly + text_h + pad * 2],
                fill=(*color, 235),
            )
            draw.text((lx + pad, ly + pad), label, font=font, fill=(255, 255, 255, 255))

    return Image.alpha_composite(base, overlay).convert("RGB")


def save_overlay(
    image: Image.Image | str | Path,
    regions: Mapping[str, Region] | Sequence[Region],
    out_path: str | Path,
) -> Path:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    render_regions(image, regions).save(path, format="PNG")
    return path


def encode_png(image: Image.Image | str | Path) -> bytes:
    buffer = io.BytesIO()
    _to_rgba(image).convert("RGB").save(buffer, format="PNG")
    return buffer.getvalue()


def crop_region(image: Image.Image, region: Region) -> Image.Image:
    return image.crop((region.xmin, region.ymin, region.xmax, region.ymax))
