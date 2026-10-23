"""T1：区域命名、坐标变换与 DPI 换算。"""

from __future__ import annotations

import pytest

from recruiting_capture.errors import ConfigError
from recruiting_capture.models.region import (
    Region,
    ScreenGeometry,
    next_region_name,
    region_index,
    region_name,
)


def test_region_name_sequence() -> None:
    assert region_name(0) == "A"
    assert region_name(25) == "Z"
    assert region_name(26) == "A1"
    assert region_name(27) == "B1"
    assert region_name(51) == "Z1"
    assert region_name(52) == "A2"


def test_region_index_roundtrip() -> None:
    for index in (0, 25, 26, 51, 52, 100):
        assert region_index(region_name(index)) == index
    assert region_index("AA") is None
    assert region_index("A0") is None
    assert region_index("") is None


def test_next_region_name_fills_gaps() -> None:
    assert next_region_name([]) == "A"
    assert next_region_name(["A", "B"]) == "C"
    assert next_region_name(["A", "C"]) == "B"
    assert next_region_name(["A", "B", "C"]) == "D"


def test_region_capture_rect_transform() -> None:
    region = Region.from_list("B", [660, 440, 1460, 930])
    assert region.capture_rect() == (660, 440, 490, 800)
    assert region.width == 800
    assert region.height == 490


def test_region_validation() -> None:
    with pytest.raises(ConfigError):
        Region.from_list("A", [100, 100, 50, 200])
    with pytest.raises(ConfigError):
        Region.from_list("A", [100, 100, 200])
    with pytest.raises(ConfigError):
        Region.from_list("A", [-1, 100, 200, 300])
    with pytest.raises(ConfigError):
        Region.from_list("", [0, 0, 100, 100])
    with pytest.raises(ConfigError):
        Region.from_list("A", [0, 0, 100, 100]).validate_bounds(50, 50)


def test_region_contains_and_center() -> None:
    region = Region.from_list("A", [100, 200, 300, 400])
    assert region.contains_point(150, 250)
    assert not region.contains_point(99, 250)


def test_screen_geometry_scaling() -> None:
    geometry = ScreenGeometry(
        screen_width=1920, screen_height=1080, shot_width=960, shot_height=540
    )
    assert geometry.scale_x == 2
    assert geometry.scale_y == 2
    assert geometry.to_screen_point(10, 20) == (20, 40)
    region = Region.from_list("A", [100, 100, 200, 200])
    assert geometry.to_screen_rect(region) == (200, 200, 200, 200)
    assert geometry.to_plan_point(200, 200) == (100, 100)


def test_screen_geometry_identity() -> None:
    geometry = ScreenGeometry(1920, 1080, 1920, 1080)
    assert geometry.is_identity
    assert geometry.to_screen_point(123, 456) == (123, 456)
