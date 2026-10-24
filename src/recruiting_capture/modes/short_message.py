"""短信模式（NEWREADME 6.2 / 8.2 / 5.3.3）。"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Mapping

from ..errors import ConfigError, LLMError, RecruitingError, StopRequested
from ..models.config import DATA_STRUCTURE_DICT, OutputConfig, SmsConfig
from ..models.region import Region
from ..text_utils import (
    clean_lines,
    clean_title,
    contains_marker,
    dedupe_lines,
    merge_unique,
    pick_title_candidate,
)
from ..ocr.engine import OcrLine
from .base import BaseRegionMode, ModeContext
from .transcribe import TranscribeMode

if TYPE_CHECKING:  # 仅用于类型注解：worker 依赖 modes.denoise_ops，运行期导入会成环
    from ..llm.worker import LLMWorker

logger = logging.getLogger(__name__)


@dataclass
class SmsItem:
    """一次列表项点击对应的采集结果。"""

    values: dict[str, Any]
    ordered_results: list[Any]
    title: str | None = None
    warnings: list[str] = field(default_factory=list)


class ShortMessageMode(BaseRegionMode):
    """面向“目录列表”的区域工作流程：点击列表项 → 调抄录模式 → 滚动。"""

    def __init__(
        self,
        region: Region,
        config: SmsConfig,
        context: ModeContext,
        transcribe_modes: Mapping[str, TranscribeMode],
    ) -> None:
        super().__init__(region, config, context)
        self.config: SmsConfig = config
        self.transcribe_modes = dict(transcribe_modes)
        self.key_region: str | None = None
        self.value_regions: list[str] = []
        self._bind_transcribe_modes()
        self.items: list[SmsItem] = []
        self.stop_reason: str = ""
        self.stop_marker: str | None = None
        self.warnings: list[str] = []
        self._processed = 0
        # 由 orchestrator 注入：保存 partial、等待前端 resume/stop，返回动作。
        self.interrupt_handler: Callable[[RecruitingError], str] | None = None

    # ------------------------------------------------------------------ #
    # 绑定（6.2 函数五）
    # ------------------------------------------------------------------ #
    def _bind_transcribe_modes(self) -> None:
        missing = [
            name for name in self.config.invoke_regions if name not in self.transcribe_modes
        ]
        if missing:
            raise ConfigError(
                f"短信区域 {self.region_name} 引用了未实例化的抄录区域：{', '.join(missing)}"
            )
        if self.config.data_structure == DATA_STRUCTURE_DICT:
            self.key_region = self.config.key_region
            if not self.key_region:
                raise ConfigError(f"短信区域 {self.region_name} 为字典模式但未指定 key_region")
            self.value_regions = [
                name for name in self.config.invoke_regions if name != self.key_region
            ]
        else:
            self.key_region = None
            self.value_regions = list(self.config.invoke_regions)

    # ------------------------------------------------------------------ #
    # 工作流程（6.2 函数十）
    # ------------------------------------------------------------------ #
    def workflow(self, max_items: int | None = None) -> dict[str, Any]:
        self.items = []
        self.stop_reason = ""
        self.stop_marker = None
        self.warnings = []
        self._processed = 0

        limit = self.config.max_items
        if max_items is not None:
            limit = max_items if limit is None else min(limit, max_items)
        if limit is None:
            limit = 10**9

        worker = self._make_worker()
        ok = False
        try:
            # 屏障必须在返回前完成：占位符一旦外泄，to_records 会把 job_desc 变成空列表
            self._collect(limit, worker)
            self._resolve_all_pending()
            self._assign_titles()
            result = self._assemble()
            ok = True
            return result
        finally:
            if worker is not None:
                abandoned = worker.close(
                    drain=ok,
                    timeout=float(getattr(self.ctx.run, "llm_drain_timeout_seconds", 30)),
                )
                self.ctx.stats["llm_calls_async"] = (
                    self.ctx.stats.get("llm_calls_async", 0) + worker.total_calls
                )
                if abandoned:
                    self.warnings.append(
                        f"{len(abandoned)} 个异步去噪未完成，已按规则兜底"
                    )

    def recover_records(self, output: OutputConfig) -> list[dict[str, Any]]:
        """中断时保存：先等待异步去噪完成，再转换已完成 items。

        暂停发生在 ``workflow()`` 内部，worker 仍存活；这里走与正常路径
        相同的 ``_resolve_all_pending()`` 屏障，等待 LLM 结果，而不是
        直接用 OCR 原文兜底。只有超时句柄才会由屏障按现有规则降级。
        """

        self._resolve_all_pending()
        self._assign_titles()
        return self.to_records(output)

    def _collect(self, limit: int, worker: "LLMWorker | None" = None) -> None:
        """点击/滚动/截图/OCR 主循环（原 workflow 的循环体）。"""

        frame, lines = self.analyze()
        round_index = 1
        while self._processed < limit and round_index <= self.config.max_rounds:
            round_texts = self.texts(lines)
            marker = contains_marker(
                round_texts,
                self.config.scroll_stop_markers,
                self.config.scroll_stop_match,
            )
            if marker:
                self.stop_marker = marker
                self.stop_reason = "list_end"
                logger.info(
                    "短信区域 %s 第 %d 轮检测到列表停止标志：%r",
                    self.region_name,
                    round_index,
                    marker,
                )
                break

            clicked = self._perform_click(lines)
            if clicked is None:
                if round_index >= self.config.max_rounds:
                    self.stop_reason = "max_rounds"
                    break
                logger.warning(
                    "短信区域 %s 第 %d 轮未找到可点击的列表文本，滚动后重试",
                    self.region_name,
                    round_index,
                )
                self.scroll_once()
                self.pause(float(self.ctx.run.scroll_pause_seconds))
                round_index += 1
                frame, lines = self.analyze()
                continue

            while True:
                try:
                    item = self._invoke_transcribe_modes(worker)
                    break
                except RecruitingError as exc:
                    handler = self.interrupt_handler
                    if handler is None:
                        raise
                    action = handler(exc)
                    if action == "resume":
                        logger.info(
                            "区域 %s 用户选择继续采集，重试当前条目",
                            self.region_name,
                        )
                        continue
                    raise StopRequested(
                        f"区域 {self.region_name} 用户选择结束采集：{exc}"
                    ) from exc
            self.items.append(item)
            self._processed += 1
            self.warnings.extend(item.warnings)
            logger.info(
                "短信区域 %s 已采集 %d 项（本次键：%s）",
                self.region_name,
                self._processed,
                item.title or "集合模式无键",
            )

            if self._processed >= limit:
                self.stop_reason = "max_items"
                break
            if round_index >= self.config.max_rounds:
                warning = (
                    f"短信区域 {self.region_name} 达到最大轮次 {self.config.max_rounds}，"
                    "已停止点击"
                )
                self.warnings.append(warning)
                logger.warning(warning)
                self.stop_reason = "max_rounds"
                break

            self.scroll_once()
            self.pause(float(self.ctx.run.scroll_pause_seconds))
            round_index += 1
            frame, lines = self.analyze()

        if not self.stop_reason:
            self.stop_reason = "max_items" if self._processed >= limit else "max_rounds"

        logger.info(
            "短信区域 %s 结束：停止原因=%s，采集 %d 项",
            self.region_name,
            self.stop_reason,
            self._processed,
        )

    # ------------------------------------------------------------------ #
    # 异步屏障（唯一等待点）
    # ------------------------------------------------------------------ #
    def _make_worker(self) -> "LLMWorker | None":
        if not getattr(self.ctx.run, "llm_async", True):
            return None
        factory = getattr(self.ctx, "llm_factory", None)
        if factory is None:
            return None  # 降级为同步，不报错
        from ..llm.worker import LLMWorker  # 延迟导入：避免 modes ↔ llm.worker 循环导入

        return LLMWorker(
            factory(),  # 独立客户端，独立 Session
            threads=int(getattr(self.ctx.run, "llm_worker_threads", 2)),
            queue_max=int(getattr(self.ctx.run, "llm_queue_max", 8)),
            task_retries=int(getattr(self.ctx.run, "llm_task_retries", 1)),
        )

    def _resolve_all_pending(self) -> None:
        """唯一屏障：按 items / invoke_regions 顺序结算占位符。"""

        from ..llm.worker import PendingText, _UNRESOLVED
        from .denoise_ops import rule_based_result

        budget = float(getattr(self.ctx.run, "llm_task_timeout_seconds", 90))
        started = time.monotonic()
        for item in self.items:
            for name in self.config.invoke_regions:
                handle = item.values.get(name)
                if not isinstance(handle, PendingText):
                    continue
                value, warnings = handle.resolve(budget)
                empty = (
                    value is _UNRESOLVED
                    or value is None
                    or (isinstance(value, str) and not value.strip())
                    or (
                        isinstance(value, (list, tuple))
                        and not any(str(part).strip() for part in value)
                    )
                )
                if empty:
                    value = rule_based_result(
                        handle.payload,
                        self.transcribe_modes[name].config.expected_format,
                    )
                    warnings = [
                        *warnings,
                        "异步去噪未完成，已用 OCR 原文兜底",
                    ]
                item.values[name] = value
                item.ordered_results = [
                    item.values[n] for n in self.config.invoke_regions
                ]
                for warning in warnings:
                    message = f"区域 {name}: {warning}"
                    item.warnings.append(message)
                    self.warnings.append(message)
        self.ctx.stats["llm_wait_ms"] = int((time.monotonic() - started) * 1000)

    def _assign_titles(self) -> None:
        """解析完成后再做键（异步下占位符不可哈希）。"""

        if self.config.data_structure != DATA_STRUCTURE_DICT or not self.key_region:
            return
        for item in self.items:
            title, extra = self._normalize_key(item.values.get(self.key_region))
            item.title = title
            item.warnings.extend(extra)

    # ------------------------------------------------------------------ #
    # 点击（6.2 函数四 + 8.2.3 可选确认/回滚）
    # ------------------------------------------------------------------ #
    def _click_point(self, lines: list[OcrLine]) -> tuple[int, int] | None:
        if self.config.click_anchor:
            return self.screen_point_from_plan(
                self.config.click_anchor[0], self.config.click_anchor[1]
            )
        for line in lines:
            if line.box[3] - line.box[1] >= 6:
                return self.line_center_on_screen(line)
        return None

    def _perform_click(self, lines: list[OcrLine]) -> tuple[int, int] | None:
        attempts = self.config.max_click_retries if self.config.verify_click else 1
        for attempt in range(1, attempts + 1):
            point = self._click_point(lines)
            if point is None:
                return None
            self.click_mouse(point)
            self.wait_after_click()

            if not self.config.verify_click:
                return point

            frame, fresh_lines = self.analyze()
            try:
                confirmation = self.ctx.llm.confirm_click(
                    texts=self.texts(fresh_lines),
                    frame=frame,
                    context={"region": self.region_name, "attempt": attempt},
                )
            except LLMError as exc:
                warning = (
                    f"短信区域 {self.region_name} 点击确认调用大模型失败，"
                    f"按 R6 降级为继续流程：{exc}"
                )
                self.warnings.append(warning)
                logger.warning(warning)
                return point

            if confirmation.matched:
                logger.info(
                    "短信区域 %s 点击确认通过：%s", self.region_name, confirmation.reason
                )
                return point

            logger.warning(
                "短信区域 %s 第 %d/%d 次点击疑似未命中（%s），以 %d 步长回滚",
                self.region_name,
                attempt,
                attempts,
                confirmation.reason,
                self.config.rollback_step,
            )
            self._rollback()
            frame, lines = self.analyze()

        raise RecruitingError(
            f"短信区域 {self.region_name} 连续 {attempts} 次点击均未命中新的列表项，"
            "已停止以避免采集错位。可关闭 verify_click 或调整区域/滚动参数。"
        )

    def _rollback(self) -> None:
        start = self.config.scroll_start
        if start:
            self.move_mouse(self.screen_point_from_plan(start[0], start[1]))
        self.ctx.driver.scroll(self.config.rollback_step)
        self.ctx.stats["scrolls"] += 1
        self.pause(float(self.ctx.run.scroll_pause_seconds))

    # ------------------------------------------------------------------ #
    # 调用抄录模式（6.2 函数六 / 5.3.3）
    # ------------------------------------------------------------------ #
    def _invoke_transcribe_modes(
        self, worker: "LLMWorker | None" = None
    ) -> SmsItem:
        values: dict[str, Any] = {}
        ordered: list[Any] = []
        warnings: list[str] = []
        for name in self.config.invoke_regions:
            mode = self.transcribe_modes[name]
            logger.info("短信区域 %s 调用抄录区域 %s", self.region_name, name)
            result = mode.workflow(worker)
            values[name] = result
            ordered.append(result)
            warnings.extend(f"区域 {name}: {warning}" for warning in mode.warnings)

        # 做键延后到 _assign_titles：异步路径下 values 里此刻是 PendingText
        return SmsItem(values=values, ordered_results=ordered, title=None, warnings=warnings)

    @staticmethod
    def _normalize_key(value: Any) -> tuple[str, list[str]]:
        warnings: list[str] = []
        if isinstance(value, str):
            title = clean_title(value)
            if title:
                return title, warnings
        if isinstance(value, (list, tuple)):
            candidate = pick_title_candidate([str(item) for item in value])
            if candidate:
                warnings.append(f"做键的抄录区域返回列表，已用规则兜底选取：{candidate}")
                return clean_title(candidate), warnings
        warnings.append("未能从做键的抄录区域取得岗位名称，已使用“缺省”作为键")
        return "缺省", warnings

    def _assemble(self) -> dict[str, Any]:
        if self.config.data_structure == DATA_STRUCTURE_DICT:
            result: dict[str, Any] = {}
            for item in self.items:
                title = item.title or "缺省"
                values = [item.values[region] for region in self.value_regions]
                flattened = self._flatten_values(values)
                if title in result:
                    existing = result[title]
                    if isinstance(existing, list) and isinstance(flattened, list):
                        result[title] = merge_unique(existing, flattened)
                    else:
                        result[title] = flattened
                    self.warnings.append(
                        f"字典模式出现重复岗位键 {title!r}，已合并其描述内容"
                    )
                    logger.warning("字典模式出现重复岗位键：%r，已合并", title)
                else:
                    result[title] = flattened
            return {self.region_name: result}

        # 集合模式：每次点击得到一个列表，整体并入大列表（5.3.3）
        return {
            self.region_name: [list(item.ordered_results) for item in self.items]
        }

    @staticmethod
    def _flatten_values(values: list[Any]) -> Any:
        if len(values) == 1 and isinstance(values[0], (list, tuple)):
            return dedupe_lines(str(item) for item in values[0])
        return list(values)

    # ------------------------------------------------------------------ #
    # 输出记录（7.3.2）
    # ------------------------------------------------------------------ #
    def to_records(self, output: OutputConfig) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for item in self.items:
            warnings = list(item.warnings)

            title_value: Any = None
            if output.title_region:
                title_value = item.values.get(output.title_region)
            if title_value is None:
                title_value = item.title
            if isinstance(title_value, (list, tuple)):
                title_value = pick_title_candidate([str(value) for value in title_value])
            title = clean_title(str(title_value)) if title_value else ""
            if not title:
                title = "缺省"
                warnings.append("岗位名称为空，已使用“缺省”占位")

            desc_value: Any = None
            if output.desc_region:
                desc_value = item.values.get(output.desc_region)
            if isinstance(desc_value, str):
                desc_lines = [desc_value]
            elif isinstance(desc_value, (list, tuple)):
                desc_lines = [str(value) for value in desc_value if str(value).strip()]
            else:
                desc_lines = []
            desc_lines = clean_lines(desc_lines, drop_noise=False)
            if not desc_lines:
                warnings.append("职位描述为空，请检查抄录区域或滚动参数")

            extra: dict[str, str | None] = {}
            for field_name, region_name in output.extra_fields.items():
                value = item.values.get(region_name)
                if isinstance(value, str):
                    extra[field_name] = clean_title(value) or None
                elif isinstance(value, (list, tuple)) and value:
                    extra[field_name] = clean_title(str(value[0])) or None
                else:
                    extra[field_name] = None

            records.append(
                {
                    "job_title": title,
                    "job_desc": desc_lines,
                    "company": extra.get("company"),
                    "salary": extra.get("salary"),
                    "warnings": warnings,
                }
            )
        return records
