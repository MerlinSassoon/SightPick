"""显示层：截图等比缩放/压缩，以及显示坐标 → 原图坐标的换算。

背景（NEWREADME 5.2）：前端画布只能在自己的可视区域内展示首屏截图。
1920×1080 甚至 3840×2160 的原图如果原样交给组件：
1) 组件会按 HTML 方式把图缩小到容器宽度，用户看不到真实像素；
2) 组件返回的是**缩小后图片的像素坐标**，若直接写入区域字典就会整体偏移。

因此本模块保证：
- 只生成一份“显示副本”，原图（坐标基准）永远不变；
- 显示副本保持宽高比，最大边不超过 ``max_width``/``max_height``；
- 提供 :class:`DisplayMapping`，把显示坐标精确还原为原图坐标。
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

logger = logging.getLogger(__name__)

SUPPORTED_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".bmp")

# 缩放比例小于该阈值时视为 1:1，避免浮点误差累积
_EPSILON = 1e-9


@dataclass(frozen=True)
class DisplayMapping:
    """显示图与原始截图的尺寸关系。

    ``original_width`` / ``original_height`` 是**坐标基准**（屏幕像素）；
    ``display_width`` / ``display_height`` 是画布实际展示的像素尺寸。
    """

    original_width: int
    original_height: int
    display_width: int
    display_height: int

    def __post_init__(self) -> None:
        for name in (
            "original_width",
            "original_height",
            "display_width",
            "display_height",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} 必须是正整数，收到：{value!r}")

    @property
    def scale(self) -> float:
        """显示像素 → 原图像素的倍数（显示图缩小时 > 1）。"""

        return self.original_width / self.display_width

    @property
    def scale_y(self) -> float:
        return self.original_height / self.display_height

    @property
    def is_identity(self) -> bool:
        return (
            self.original_width == self.display_width
            and self.original_height == self.display_height
        )

    @property
    def review_ratio(self) -> float:
        """显示图面积占原图面积的比例（0~1），用于压缩策略。"""

        return (self.display_width * self.display_height) / (
            self.original_width * self.original_height
        )

    def to_original_x(self, x: float) -> int:
        value = int(round(float(x) * self.original_width / self.display_width))
        return max(0, min(self.original_width, value))

    def to_original_y(self, y: float) -> int:
        value = int(round(float(y) * self.original_height / self.display_height))
        return max(0, min(self.original_height, value))

    def to_original_point(self, x: float, y: float) -> tuple[int, int]:
        return self.to_original_x(x), self.to_original_y(y)

    def to_original_rect(
        self, x1: float, y1: float, x2: float, y2: float
    ) -> tuple[int, int, int, int]:
        """显示矩形 → 原图矩形（已排序、已裁剪到原图范围）。"""

        ox1, oy1 = self.to_original_point(min(x1, x2), min(y1, y2))
        ox2, oy2 = self.to_original_point(max(x1, x2), max(y1, y2))
        return ox1, oy1, ox2, oy2

    def to_display_point(self, x: float, y: float) -> tuple[float, float]:
        return (
            float(x) * self.display_width / self.original_width,
            float(y) * self.display_height / self.original_height,
        )


def compute_display_size(
    width: int,
    height: int,
    max_width: int,
    max_height: int,
) -> tuple[int, int, DisplayMapping]:
    """按等比缩放计算显示尺寸；不会放大超过原图。"""

    if width <= 0 or height <= 0:
        raise ValueError(f"图片尺寸必须为正：{width}x{height}")
    if max_width <= 0 or max_height <= 0:
        raise ValueError(f"最大展示尺寸必须为正：{max_width}x{max_height}")

    if width <= max_width and height <= max_height:
        display_width, display_height = width, height
    else:
        ratio = min(max_width / width, max_height / height)
        display_width = max(1, int(round(width * ratio)))
        display_height = max(1, int(round(height * ratio)))

    mapping = DisplayMapping(width, height, display_width, display_height)
    if mapping.scale < 1 - _EPSILON or mapping.scale_y < 1 - _EPSILON:
        raise AssertionError("显示尺寸不应大于原图")
    return display_width, display_height, mapping


def make_display_copy(
    source: str | Path,
    dest: str | Path,
    *,
    max_width: int = 1280,
    max_height: int = 800,
    jpeg_quality: int = 85,
    suffix: str | None = None,
) -> DisplayMapping:
    """生成用于前端展示的压缩副本（原图不变），并返回坐标换算关系。

    - 大图（缩放到 75% 以下）导出为 JPEG（``jpeg_quality``），其余用 PNG；
    - ``suffix`` 可强制输出格式；为空时按缩放比例自动选择；
    - 若显示尺寸与原图一致，仍然写一份副本，保证前端加载的是“与坐标一一对应”的文件。
    """

    source_path = Path(source)
    dest_path = Path(dest)
    forced = (suffix or "").lower()
    if forced and not forced.startswith("."):
        forced = "." + forced
    if forced and forced not in SUPPORTED_SUFFIXES:
        raise ValueError(f"不支持的图片后缀：{forced}")

    with Image.open(source_path) as image:
        image.load()
        original = image.size
        display_width, display_height, mapping = compute_display_size(
            original[0], original[1], max_width, max_height
        )

        if mapping.is_identity:
            resized = image.copy()
        else:
            resized = image.resize(
                (display_width, display_height), Image.Resampling.LANCZOS
            )
        if resized.mode in ("RGBA", "LA", "P"):
            rgba = resized.convert("RGBA")
            background = Image.new("RGB", rgba.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.split()[-1])
            resized = background
        elif resized.mode != "RGB":
            resized = resized.convert("RGB")

        final_suffix = forced or (".jpg" if mapping.review_ratio < 0.75 else ".png")
        # 未显式指定输出格式时，按压缩策略决定扩展名（大图缩小 → JPEG）
        explicit_suffix = dest_path.suffix.lower() in SUPPORTED_SUFFIXES
        if forced:
            final_suffix = forced
        elif explicit_suffix:
            final_suffix = dest_path.suffix.lower()
        final_path = dest_path if explicit_suffix else dest_path.with_suffix(final_suffix)
        final_suffix = final_path.suffix.lower()

        final_path.parent.mkdir(parents=True, exist_ok=True)
        if final_suffix in (".jpg", ".jpeg"):
            resized.save(
                final_path, format="JPEG", quality=jpeg_quality, optimize=True
            )
        else:
            resized.save(final_path, format="PNG", optimize=True)

    logger.debug(
        "显示副本：%s %sx%s → %s %sx%s（≈%.0f%%）",
        source_path.name,
        mapping.original_width,
        mapping.original_height,
        final_path.name,
        mapping.display_width,
        mapping.display_height,
        mapping.review_ratio * 100,
    )
    return mapping


def is_image_file(path: str | Path) -> bool:
    return Path(path).suffix.lower() in SUPPORTED_SUFFIXES


def image_size(path: str | Path) -> tuple[int, int]:
    with Image.open(path) as image:
        return image.size


def data_url_for_image(path: str | Path) -> str:
    """（调试用）返回 data URL；前端组件使用路径输入，不需要它。"""

    import base64

    raw = Path(path).read_bytes()
    suffix = Path(path).suffix.lower()
    mime = "image/jpeg" if suffix in (".jpg", ".jpeg") else "image/png"
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def encode_display_bytes(
    image: Image.Image,
    *,
    jpeg_quality: int = 85,
    force_jpeg: bool = False,
) -> tuple[bytes, str]:
    """把一张（已缩放的）图片编码为前端可直接加载的字节流，返回 (数据, MIME)。

    压缩失败时回退为无损 PNG，绝不返回不可用的数据。
    """

    buffer = io.BytesIO()
    try:
        if force_jpeg and image.mode in ("RGB", "L"):
            image.save(buffer, format="JPEG", quality=jpeg_quality, optimize=True)
            return buffer.getvalue(), "image/jpeg"
    except Exception as exc:  # pragma: no cover - 编码器异常时回退
        logger.warning("JPEG 编码失败，回退 PNG：%s", exc)
        buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue(), "image/png"


def describe_mapping(mapping: DisplayMapping) -> str:
    """给 UI 用的一行说明。"""

    if mapping.is_identity:
        return f"原图 {mapping.original_width}×{mapping.original_height}（1:1 显示，坐标无需换算）"
    return (
        f"原图 {mapping.original_width}×{mapping.original_height} → "
        f"展示 {mapping.display_width}×{mapping.display_height}"
        f"（缩放 {mapping.scale:.3f}×，框选坐标已按比例还原，精度约 ±{mapping.scale:.1f} px）"
    )


def mapping_to_dict(mapping: DisplayMapping) -> dict[str, Any]:
    return {
        "original": [mapping.original_width, mapping.original_height],
        "display": [mapping.display_width, mapping.display_height],
        "scale": round(mapping.scale, 6),
        "identity": mapping.is_identity,
    }


def mapping_from_dict(data: dict[str, Any]) -> DisplayMapping:
    original = data["original"]
    display = data["display"]
    return DisplayMapping(
        original_width=int(original[0]),
        original_height=int(original[1]),
        display_width=int(display[0]),
        display_height=int(display[1]),
    )
