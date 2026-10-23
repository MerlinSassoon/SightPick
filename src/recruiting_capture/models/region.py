"""区域、区域命名与坐标变换（NEWREADME 5.3.1 / 6.1 函数七）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from ..errors import ConfigError

_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def region_name(index: int) -> str:
    """区域序号 → 区域名：A..Z，之后 A1..Z1，再之后 A2..Z2……（5.3.1）。"""

    if not isinstance(index, int) or index < 0:
        raise ConfigError(f"区域序号必须是非负整数，收到：{index!r}")
    block, offset = divmod(index, len(_ALPHABET))
    return _ALPHABET[offset] + ("" if block == 0 else str(block))


def region_index(name: str) -> int | None:
    """区域名 → 序号；无法识别时返回 None。"""

    if not name:
        return None
    letter = name[0]
    if letter not in _ALPHABET:
        return None
    suffix = name[1:]
    if suffix == "":
        block = 0
    elif suffix.isdigit() and int(suffix) >= 1:
        block = int(suffix)
    else:
        return None
    index = block * len(_ALPHABET) + _ALPHABET.index(letter)
    return index if region_name(index) == name else None


def next_region_name(existing: Iterable[str]) -> str:
    """取最小的未使用区域名（删除后可复用）。"""

    used = set(existing)
    index = 0
    while region_name(index) in used:
        index += 1
    return region_name(index)


def _as_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ConfigError(f"{field} 必须是整数，不能是布尔值")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    raise ConfigError(f"{field} 必须是整数，收到：{value!r}")


@dataclass(frozen=True)
class Region:
    """区域坐标向量 [xmin, ymin, xmax, ymax]（截图/屏幕像素）。"""

    name: str
    xmin: int
    ymin: int
    xmax: int
    ymax: int

    @classmethod
    def from_list(cls, name: str, coords: Sequence[Any]) -> "Region":
        if not isinstance(name, str) or not name.strip():
            raise ConfigError(f"区域名必须是非空字符串，收到：{name!r}")
        if not isinstance(coords, (list, tuple)) or len(coords) != 4:
            raise ConfigError(f"区域 {name} 的坐标必须是 [xmin, ymin, xmax, ymax] 四元组，收到：{coords!r}")
        xmin, ymin, xmax, ymax = (_as_int(v, f"区域 {name} 坐标") for v in coords)
        if xmin >= xmax or ymin >= ymax:
            raise ConfigError(
                f"区域 {name} 坐标非法：需满足 xmin < xmax 且 ymin < ymax，收到 {coords!r}"
            )
        if xmin < 0 or ymin < 0:
            raise ConfigError(f"区域 {name} 坐标不能为负，收到 {coords!r}")
        return cls(name=name.strip(), xmin=xmin, ymin=ymin, xmax=xmax, ymax=ymax)

    @property
    def width(self) -> int:
        return self.xmax - self.xmin

    @property
    def height(self) -> int:
        return self.ymax - self.ymin

    def as_list(self) -> list[int]:
        return [self.xmin, self.ymin, self.xmax, self.ymax]

    def capture_rect(self) -> tuple[int, int, int, int]:
        """[xmin, ymin, xmax, ymax] → [xmin, ymin, height, width]（6.1 坐标变换）。"""

        return self.xmin, self.ymin, self.height, self.width

    def contains_point(self, x: float, y: float) -> bool:
        return self.xmin <= x <= self.xmax and self.ymin <= y <= self.ymax

    def validate_bounds(self, screen_width: int, screen_height: int) -> None:
        if self.xmax > screen_width or self.ymax > screen_height:
            raise ConfigError(
                f"区域 {self.name} 超出屏幕范围：{self.as_list()} 不在 "
                f"{screen_width}x{screen_height} 内，请重新框选或检查 DPI 缩放"
            )

    def changed(self, coords: Sequence[Any]) -> "Region":
        return Region.from_list(self.name, coords)


@dataclass(frozen=True)
class ScreenGeometry:
    """截图像素坐标 ↔ 屏幕坐标的缩放换算（R1：DPI/分辨率变化）。"""

    screen_width: int
    screen_height: int
    shot_width: int
    shot_height: int

    def __post_init__(self) -> None:
        for field_name, value in (
            ("screen_width", self.screen_width),
            ("screen_height", self.screen_height),
            ("shot_width", self.shot_width),
            ("shot_height", self.shot_height),
        ):
            if not isinstance(value, int) or value <= 0:
                raise ConfigError(f"{field_name} 必须是正整数，收到：{value!r}")

    @property
    def scale_x(self) -> float:
        return self.screen_width / self.shot_width

    @property
    def scale_y(self) -> float:
        return self.screen_height / self.shot_height

    @property
    def is_identity(self) -> bool:
        return self.screen_width == self.shot_width and self.screen_height == self.shot_height

    def to_screen_point(self, x: float, y: float) -> tuple[int, int]:
        """截图/计划像素 → 屏幕逻辑坐标（供 moveTo/click 使用）。"""

        return int(round(x * self.scale_x)), int(round(y * self.scale_y))

    def to_screen_rect(self, region: Region) -> tuple[int, int, int, int]:
        """区域 → pyautogui screenshot(region=...) 所需的屏幕矩形。"""

        x, y = self.to_screen_point(region.xmin, region.ymin)
        width = max(1, int(round(region.width * self.scale_x)))
        height = max(1, int(round(region.height * self.scale_y)))
        return x, y, width, height

    def to_plan_point(self, screen_x: float, screen_y: float) -> tuple[int, int]:
        return int(round(screen_x / self.scale_x)), int(round(screen_y / self.scale_y))
