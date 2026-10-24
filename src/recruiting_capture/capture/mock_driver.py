"""确定性 mock 屏幕驱动：用于 demo、单元测试与无桌面环境验证。

它按 ``demo_story.json`` 场景渲染合成画面，并通过 ``Frame.meta`` 把
“本应被 OCR 识别到的文本行及坐标”传给 :class:`MockOcrEngine`。
生产路径永远不依赖本模块（见 docs/AMBIGUITIES.md 的“附加项”）。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Mapping

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..errors import ConfigError
from ..models.region import Region
from .driver import Frame

logger = logging.getLogger(__name__)

CARD_HEIGHT = 210
LINE_STEP = 34
TEXT_X = 18
DEFAULT_SCORE = 0.99
LOW_SCORE = 0.35
FOOTER_GAP = 600


class MockScreenDriver:
    """按场景剧本生成画面的驱动替身。"""

    def __init__(
        self,
        scenario: Mapping[str, Any],
        regions: Mapping[str, Region],
        *,
        screen_size: tuple[int, int] = (1920, 1080),
        px_per_scroll_unit: float = 1.0,
    ) -> None:
        if not isinstance(scenario, Mapping):
            raise ConfigError("mock 场景必须是 JSON 对象")
        jobs = scenario.get("jobs")
        if not isinstance(jobs, list) or not jobs:
            raise ConfigError("mock 场景 jobs 不能为空")
        for index, job in enumerate(jobs):
            if not isinstance(job, Mapping) or not job.get("title"):
                raise ConfigError(f"mock 场景第 {index + 1} 个岗位缺少 title")
            if not isinstance(job.get("desc_pages"), list) or not job["desc_pages"]:
                raise ConfigError(f"mock 场景岗位 {job.get('title')!r} 缺少 desc_pages")
        self.scenario = dict(scenario)
        self.jobs: list[Mapping[str, Any]] = list(jobs)
        self.regions = dict(regions)
        self.screen_size = (int(screen_size[0]), int(screen_size[1]))
        self.px_per_scroll_unit = float(px_per_scroll_unit)
        self.offsets: dict[str, float] = {"A": 0.0, "B": 0.0}
        self.selected_job = 0
        self.click_is_new = True
        self.position: tuple[int, int] = (0, 0)
        self.clicks: list[tuple[int, int]] = []
        self.scroll_events: list[tuple[str | None, int]] = []
        self.footer_y = len(self.jobs) * CARD_HEIGHT + FOOTER_GAP
        self._font = ImageFont.load_default()

    @classmethod
    def from_file(
        cls,
        path: str | Path,
        regions: Mapping[str, Region],
        *,
        screen_size: tuple[int, int] = (1920, 1080),
        px_per_scroll_unit: float = 1.0,
    ) -> "MockScreenDriver":
        import json

        scenario_path = Path(path)
        if not scenario_path.is_file():
            raise ConfigError(f"mock 场景文件不存在：{scenario_path}")
        try:
            scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"mock 场景不是合法 JSON：{scenario_path}（{exc}）") from exc
        return cls(
            scenario,
            regions,
            screen_size=screen_size,
            px_per_scroll_unit=px_per_scroll_unit,
        )

    # ------------------------------------------------------------------ #
    # ScreenDriver 协议
    # ------------------------------------------------------------------ #
    def size(self) -> tuple[int, int]:
        return self.screen_size

    def close(self) -> None:
        """与真实驱动保持同样的生命周期接口（mock 没有后台线程）。"""

    def move_to(self, x: int, y: int, duration: float = 0.01) -> None:
        self.position = (int(x), int(y))

    def click(self, x: int | None = None, y: int | None = None) -> None:
        if x is None or y is None:
            x, y = self.position
        self.clicks.append((int(x), int(y)))
        label = self._region_at((int(x), int(y)))
        if label == "A":
            region = self.regions["A"]
            index = int((int(y) - region.ymin + self.offsets["A"]) // CARD_HEIGHT)
            if 0 <= index < len(self.jobs):
                self.click_is_new = index != self.selected_job
                self.selected_job = index
                # 切换到新岗位时，右侧详情页回到顶部
                self.offsets["B"] = 0.0
                logger.debug("mock 点击列表项：index=%s click_is_new=%s", index, self.click_is_new)

    def scroll(self, amount: int) -> None:
        label = self._region_at(self.position)
        self.scroll_events.append((label, int(amount)))
        if label not in ("A", "B"):
            logger.debug("mock 滚动位置不在 A/B 区域：position=%s", self.position)
            return
        delta = -int(amount) * self.px_per_scroll_unit
        current = self.offsets[label] + delta
        self.offsets[label] = max(0.0, min(current, self._max_offset(label)))
        logger.debug("mock 滚动 %s：amount=%s offset=%.1f", label, amount, self.offsets[label])

    def capture(self, rect: tuple[int, int, int, int], label: str | None = None) -> Frame:
        resolved = label or self._region_by_rect(rect)
        x, y, width, height = (int(v) for v in rect)
        image = Image.new("RGB", (width, height), (246, 247, 249))
        draw = ImageDraw.Draw(image)
        lines = self._render(resolved, draw, width, height)
        return Frame(
            image=np.array(image),
            region=(x, y, width, height),
            label=resolved,
            meta={
                "lines": lines,
                "selected_job": self.selected_job,
                "click_is_new": self.click_is_new,
                "offset": self.offsets.get(resolved or "", 0.0),
                "screen": self.screen_size,
            },
        )

    # ------------------------------------------------------------------ #
    # 渲染
    # ------------------------------------------------------------------ #
    def _render(
        self,
        label: str | None,
        draw: ImageDraw.ImageDraw,
        width: int,
        height: int,
    ) -> list[dict[str, Any]]:
        if label == "A":
            return self._render_list(draw, width, height)
        if label == "B":
            return self._render_description(draw, width, height)
        if label == "C":
            return self._render_header(draw, width, height)
        return self._render_generic(draw, width, height)

    def _emit(
        self,
        draw: ImageDraw.ImageDraw,
        lines: list[dict[str, Any]],
        text: str,
        x: int,
        y: float,
        width: int,
        height: int,
        *,
        score: float = DEFAULT_SCORE,
    ) -> None:
        if y < -LINE_STEP or y > height:
            return
        clipped_y = max(0, int(y))
        try:
            bbox = draw.textbbox((x, clipped_y), text, font=self._font)
            text_width = bbox[2] - bbox[0]
            text_height = max(12, bbox[3] - bbox[1])
        except Exception:  # pragma: no cover
            text_width, text_height = len(text) * 8, 14
        x2 = min(width, x + max(text_width, 8))
        y2 = min(height, clipped_y + text_height)
        draw.text((x, clipped_y), text, font=self._font, fill=(34, 38, 45))
        lines.append(
            {
                "text": text,
                "score": score,
                "box": [x, clipped_y, x2, y2],
            }
        )

    def _render_list(
        self, draw: ImageDraw.ImageDraw, width: int, height: int
    ) -> list[dict[str, Any]]:
        lines: list[dict[str, Any]] = []
        offset = self.offsets["A"]
        for index, job in enumerate(self.jobs):
            base = index * CARD_HEIGHT - offset
            rows = [
                (str(job["title"]), 15),
                (str(job.get("salary", "")), 55),
                (" · ".join(str(tag) for tag in job.get("tags", [])), 90),
                (str(job.get("company", "")), 125),
                (str(job.get("city", "")), 158),
            ]
            for text, top in rows:
                if text:
                    self._emit(draw, lines, text, TEXT_X, base + top, width, height)
            self._emit(draw, lines, "☆", max(0, width - 30), base + 5, width, height, score=LOW_SCORE)
        footer_offset = self.footer_y - offset
        if footer_offset <= height:
            for row, text in enumerate(
                self.scenario.get("list_footer", ["企业服务", "使用与帮助"])
            ):
                self._emit(
                    draw, lines, str(text), TEXT_X, footer_offset + 8 + row * LINE_STEP, width, height
                )
        return lines

    def _render_description(
        self, draw: ImageDraw.ImageDraw, width: int, height: int
    ) -> list[dict[str, Any]]:
        lines: list[dict[str, Any]] = []
        job = self.jobs[self.selected_job]
        texts: list[str] = []
        for page in job.get("desc_pages", []):
            texts.extend(str(item) for item in page)
        texts.extend(str(item) for item in self.scenario.get("noise_after_desc", []))
        offset = self.offsets["B"]
        for index, text in enumerate(texts):
            y = 10 + index * LINE_STEP - offset
            self._emit(draw, lines, text, TEXT_X, y, width, height)
        self._emit(draw, lines, "☆", max(0, width - 30), 6, width, height, score=LOW_SCORE)
        return lines

    def _render_header(
        self, draw: ImageDraw.ImageDraw, width: int, height: int
    ) -> list[dict[str, Any]]:
        lines: list[dict[str, Any]] = []
        job = self.jobs[self.selected_job]
        rows = [
            (str(job["title"]), 6, DEFAULT_SCORE),
            (str(job.get("salary", "")), 36, DEFAULT_SCORE),
            (" · ".join(str(tag) for tag in job.get("tags", [])), 66, DEFAULT_SCORE),
            ("收藏", 6, LOW_SCORE),
        ]
        for text, top, score in rows:
            if text:
                self._emit(draw, lines, text, TEXT_X, top, width, height, score=score)
        return lines

    def _render_generic(
        self, draw: ImageDraw.ImageDraw, width: int, height: int
    ) -> list[dict[str, Any]]:
        lines: list[dict[str, Any]] = []
        self._emit(draw, lines, "未配置区域", TEXT_X, 10, width, height)
        return lines

    # ------------------------------------------------------------------ #
    # 内部工具
    # ------------------------------------------------------------------ #
    def _max_offset(self, label: str) -> float:
        if label == "A":
            return float(len(self.jobs) * CARD_HEIGHT)
        job = self.jobs[self.selected_job]
        count = sum(len(page) for page in job.get("desc_pages", []))
        count += len(self.scenario.get("noise_after_desc", []))
        return max(0.0, count * LINE_STEP + 20)

    def _region_at(self, point: tuple[int, int]) -> str | None:
        for name, region in self.regions.items():
            if region.contains_point(point[0], point[1]):
                return name
        return None

    def _region_by_rect(self, rect: tuple[int, int, int, int]) -> str | None:
        target = tuple(int(v) for v in rect)
        for name, region in self.regions.items():
            if (region.xmin, region.ymin, region.width, region.height) == target:
                return name
        return None
