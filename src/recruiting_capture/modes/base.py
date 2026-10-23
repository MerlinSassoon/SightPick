"""两种区域模式的公共能力（NEWREADME 6.1 / 6.2 / 6.3 同名函数）。"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..capture.driver import Frame, ScreenDriver
from ..llm.client import LLMClient
from ..models.config import RegionConfig, RunConfig
from ..models.region import Region, ScreenGeometry
from ..ocr.engine import OcrEngine, OcrLine

logger = logging.getLogger("recruiting_capture.modes")


@dataclass
class ModeContext:
    """模式实例共享的依赖与运行参数。"""

    driver: ScreenDriver
    ocr: OcrEngine
    llm: LLMClient
    run: RunConfig
    geometry: ScreenGeometry
    logger: logging.Logger = field(default_factory=lambda: logger)
    sleep: Callable[[float], None] = time.sleep
    screenshots_dir: Path | None = None
    save_screenshots: bool = False
    stats: dict[str, int] = field(
        default_factory=lambda: {
            "screenshots": 0,
            "ocr_lines": 0,
            "clicks": 0,
            "scrolls": 0,
        }
    )


class BaseRegionMode:
    """共享：移动/滚动/点击/截图裁切/坐标换算。"""

    def __init__(self, region: Region, config: RegionConfig, context: ModeContext) -> None:
        self.region = region
        self.config = config
        self.ctx = context
        self.capture_rect = context.geometry.to_screen_rect(region)
        self._last_frame: Frame | None = None
        self._last_lines: list[OcrLine] = []
        self._screenshot_seq = 0
        self._scroll_distance_override: int | None = None

    # ------------------------------------------------------------------ #
    # 基础动作
    # ------------------------------------------------------------------ #
    @property
    def region_name(self) -> str:
        return self.region.name

    def screenshot(self) -> Frame:
        frame = self.ctx.driver.capture(self.capture_rect, label=self.region.name)
        self.ctx.stats["screenshots"] += 1
        self._last_frame = frame
        if self.ctx.save_screenshots and self.ctx.screenshots_dir is not None:
            self._save_screenshot(frame)
        return frame

    def analyze(self) -> tuple[Frame, list[OcrLine]]:
        frame = self.screenshot()
        lines = self.ctx.ocr.recognize(
            frame, threshold=self.ctx.run.ocr_confidence_threshold
        )
        self.ctx.stats["ocr_lines"] += len(lines)
        self._last_lines = lines
        return frame, lines

    def move_mouse(self, point: tuple[int, int]) -> None:
        self.ctx.driver.move_to(int(point[0]), int(point[1]))
        logger.debug("区域 %s 移动鼠标到 %s", self.region_name, point)

    def click_mouse(self, point: tuple[int, int]) -> None:
        self.ctx.driver.click(int(point[0]), int(point[1]))
        self.ctx.stats["clicks"] += 1
        logger.info("区域 %s 点击坐标 %s", self.region_name, point)

    def scroll_mouse(self, amount: int | None = None) -> None:
        value = int(amount if amount is not None else self.scroll_distance)
        self.ctx.driver.scroll(value)
        self.ctx.stats["scrolls"] += 1

    def scroll_once(self) -> None:
        """移动滚动起始坐标并滚动本区域（6.1 / 6.2 函数二）。"""

        start = getattr(self.config, "scroll_start", None)
        if start:
            self.move_mouse(self.screen_point_from_plan(start[0], start[1]))
        logger.info("区域 %s 滚动 %s", self.region_name, self.scroll_distance)
        self.scroll_mouse()
        self.action_pause()

    @property
    def scroll_distance(self) -> int:
        if self._scroll_distance_override is not None:
            return self._scroll_distance_override
        return int(getattr(self.config, "scroll_distance", 0) or 0)

    def pause(self, seconds: float) -> None:
        if seconds and seconds > 0:
            self.ctx.sleep(float(seconds))

    def action_pause(self) -> None:
        """R4：在动作之间加入随机间隔，降低自动化被识别的概率。"""

        low = max(0.0, float(self.ctx.run.min_action_interval_seconds))
        high = max(low, float(self.ctx.run.max_action_interval_seconds))
        self.pause(random.uniform(low, high))

    def wait_after_click(self) -> None:
        self.pause(float(self.ctx.run.click_pause_seconds))

    # ------------------------------------------------------------------ #
    # 坐标换算
    # ------------------------------------------------------------------ #
    def screen_point_from_plan(self, x: float, y: float) -> tuple[int, int]:
        """计划/截图坐标 → 屏幕坐标（点击、滚动起点）。"""

        return self.ctx.geometry.to_screen_point(x, y)

    def screen_point_from_local(self, x: float, y: float) -> tuple[int, int]:
        """裁切图内坐标 → 屏幕坐标。"""

        return int(round(self.capture_rect[0] + x)), int(round(self.capture_rect[1] + y))

    def line_center_on_screen(self, line: OcrLine) -> tuple[int, int]:
        cx, cy = line.center
        return self.screen_point_from_local(cx, cy)

    # ------------------------------------------------------------------ #
    # 小工具
    # ------------------------------------------------------------------ #
    @staticmethod
    def texts(lines: list[OcrLine]) -> list[str]:
        return [line.text for line in lines]

    def _save_screenshot(self, frame: Frame) -> None:
        try:
            from PIL import Image

            self._screenshot_seq += 1
            assert self.ctx.screenshots_dir is not None
            path = (
                self.ctx.screenshots_dir
                / f"{self.region_name}_{self._screenshot_seq:03d}.png"
            )
            Image.fromarray(frame.image).save(path)
        except Exception as exc:  # pragma: no cover - 调试功能失败不应中断采集
            logger.warning("保存调试截图失败：%s", exc)
