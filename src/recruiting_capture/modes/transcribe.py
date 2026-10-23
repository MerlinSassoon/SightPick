"""抄录模式（NEWREADME 6.3 / 8.3）。"""

from __future__ import annotations

import logging

from ..capture.driver import Frame
from ..errors import AnchorNotFoundError, LLMError
from ..models.config import TranscribeConfig
from ..models.region import Region
from ..ocr.engine import OcrLine
from ..text_utils import (
    compact,
    contains_marker,
    dedupe_lines,
)
from .base import BaseRegionMode, ModeContext
from .denoise_ops import apply_expected_format, denoise_once, rule_based_result

logger = logging.getLogger(__name__)


class TranscribeMode(BaseRegionMode):
    """面向“文本块”的区域工作流程。"""

    def __init__(self, region: Region, config: TranscribeConfig, context: ModeContext) -> None:
        super().__init__(region, config, context)
        self.config: TranscribeConfig = config
        self._effective_anchor: str | None = config.anchor_key
        self.stop_marker: str | None = None
        self.rounds_used: int = 0
        self.warnings: list[str] = []

    # ------------------------------------------------------------------ #
    # 工作流程（6.3 函数八）
    # ------------------------------------------------------------------ #
    def workflow(self) -> str | list[str] | tuple[str, ...]:
        self.stop_marker = None
        self.rounds_used = 0
        self.warnings = []
        self._effective_anchor = self.config.anchor_key
        self._scroll_distance_override = None

        if not self.config.scrollable:
            return self._run_single()
        return self._run_scrollable()

    def _run_single(self) -> str | list[str] | tuple[str, ...]:
        frame, lines = self.analyze()
        self.rounds_used = 1
        if self._effective_anchor:
            self._ensure_anchor(frame, lines)
        return self._denoise([line.text for line in lines], frame)

    def _run_scrollable(self) -> str | list[str] | tuple[str, ...]:
        collected: list[str] = []
        seen: set[str] = set()
        stale_rounds = 0
        last_frame: Frame | None = None
        config = self.config

        for round_index in range(1, config.max_rounds + 1):
            frame, lines = self.analyze()
            self.rounds_used = round_index
            last_frame = frame

            if round_index == 1 and self._effective_anchor:
                self._ensure_anchor(frame, lines)

            round_texts = [line.text for line in lines]
            new_texts: list[str] = []
            for text in round_texts:
                key = compact(text)
                if key and key not in seen:
                    seen.add(key)
                    collected.append(text)
                    new_texts.append(text)

            stale_rounds = stale_rounds + 1 if not new_texts else 0

            marker = contains_marker(
                round_texts, config.scroll_stop_markers, config.scroll_stop_match
            )
            if marker:
                self.stop_marker = marker
                logger.info(
                    "区域 %s 第 %d 轮命中滚动停止标志：%r",
                    self.region_name,
                    round_index,
                    marker,
                )
                break

            if stale_rounds >= config.max_stale_rounds:
                warning = (
                    f"区域 {self.region_name} 连续 {stale_rounds} 轮没有新增文本，"
                    "为防止死循环已提前结束（可能是页面已到底或滚动距离不当）"
                )
                self.warnings.append(warning)
                logger.warning(warning)
                break

            if round_index >= config.max_rounds:
                warning = (
                    f"区域 {self.region_name} 达到最大滚动轮次 {config.max_rounds}，"
                    "仍未命中滚动停止标志，已按部分内容结束"
                )
                self.warnings.append(warning)
                logger.warning(warning)
                break

            self.scroll_once()
            self.pause(float(self.ctx.run.scroll_pause_seconds))

        logger.info(
            "区域 %s 采集 %d 轮，累计 %d 行文本",
            self.region_name,
            self.rounds_used,
            len(collected),
        )
        return self._denoise(dedupe_lines(collected), last_frame)

    # ------------------------------------------------------------------ #
    # 首轮锚点确认（8.3.2 / 6.5.3）
    # ------------------------------------------------------------------ #
    def _ensure_anchor(self, frame: Frame, lines: list[OcrLine]) -> None:
        anchor = self._effective_anchor
        if not anchor:
            return
        texts = self.texts(lines)
        if contains_marker(texts, [anchor], "substring"):
            logger.debug("区域 %s 首轮直接命中锚点 %r", self.region_name, anchor)
            return

        logger.info(
            "区域 %s 首轮未在 OCR 文本中发现锚点 %r，交由视觉大模型确认",
            self.region_name,
            anchor,
        )
        try:
            confirmation = self.ctx.llm.confirm_anchor(
                texts=texts,
                frame=frame,
                anchor=anchor,
                context={"region": self.region_name},
            )
        except LLMError as exc:
            warning = (
                f"区域 {self.region_name} 锚点确认调用大模型失败，按 R6 降级为纯 OCR 继续：{exc}"
            )
            self.warnings.append(warning)
            logger.warning(warning)
            return

        if not confirmation.exists:
            raise AnchorNotFoundError(
                f"区域 {self.region_name} 首轮未识别到锚点“{anchor}”，"
                "视觉大模型确认页面中不存在该内容。请检查区域框选或页面状态。"
            )

        if confirmation.corrected_key:
            corrected = confirmation.corrected_key
            logger.info(
                "区域 %s 锚点经大模型修正：%r → %r（%s）",
                self.region_name,
                anchor,
                corrected,
                confirmation.reason,
            )
            self._effective_anchor = corrected

        if confirmation.box:
            self._maybe_adjust_scroll(confirmation.box, confirmation.reason)

    def _maybe_adjust_scroll(self, box: list[int], reason: str) -> None:
        """6.5.3“加权计算滚动距离”。

        设计未给出公式，本实现按“锚点到区域底部的距离 × 1.5（0.5 权）”估算，
        默认只记录建议值，不覆盖用户校准值（见 docs/AMBIGUITIES.md D10）。
        """

        region_height = self.capture_rect[3]
        bottom_gap = max(0, region_height - int(box[3]))
        weight = 1.5
        suggested_px = bottom_gap * weight
        units = max(1, int(round(suggested_px / max(1e-6, self.ctx.run.px_per_scroll_unit))))
        message = (
            f"区域 {self.region_name} 锚点建议滚动距离 ≈ {units}（底部余量 {bottom_gap}px，"
            f"×{weight} 权；{reason or '大模型返回坐标'}）"
        )
        logger.info(message)
        if self.config.auto_adjust_scroll:
            self._scroll_distance_override = -abs(units)
            logger.info("已按配置 auto_adjust_scroll 应用建议滚动距离：%s", -abs(units))
        else:
            self.warnings.append(message + "；未自动应用，可在区域配置中手动填写")

    # ------------------------------------------------------------------ #
    # 结果去噪（6.3 函数八点零）
    # ------------------------------------------------------------------ #
    def _denoise(
        self, texts: list[str], frame: Frame | None
    ) -> str | list[str] | tuple[str, ...]:
        payload = dedupe_lines(texts)
        result, warnings = denoise_once(
            self.ctx.llm,
            texts=payload,
            frame=frame,
            expected_content=self.config.expected_content,
            expected_format=self.config.expected_format,
            context={"region": self.region_name},
        )
        for warning in warnings:
            message = f"区域 {self.region_name} {warning}"
            self.warnings.append(message)
            logger.warning(message)
        return result

    def _rule_based_result(
        self, texts: list[str]
    ) -> str | list[str]:
        return rule_based_result(texts, self.config.expected_format)

    def _apply_format(
        self,
        result: str | list[str] | tuple[str, ...],
        fallback_texts: list[str],
    ) -> str | list[str] | tuple[str, ...]:
        return apply_expected_format(result, fallback_texts, self.config.expected_format)
