"""与 Streamlit 无关的计划构建逻辑，便于单元测试。

前端只负责把控件值写入这里；所有命名、默认值、校验都在本模块完成。
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .errors import ConfigError
from .models.config import (
    DATA_STRUCTURE_DICT,
    MODE_SMS,
    MODE_TRANSCRIBE,
    CapturePlan,
    OutputConfig,
    RunConfig,
    ScreenInfo,
    SmsConfig,
    TranscribeConfig,
    default_sms_config,
    default_transcribe_config,
)
from .models.plan_io import plan_from_dict
from .models.region import Region, next_region_name

_ALLOWED_SMS_KEYS = set(SmsConfig.FIELDS)
_ALLOWED_TRANSCRIBE_KEYS = set(TranscribeConfig.FIELDS)


class PlanBuilder:
    """内存中的计划草稿。

    区域与配置以普通字典保存，方便直接回填 Streamlit 控件；
    :meth:`build` 时再转换为强类型 :class:`CapturePlan` 并完整校验。
    """

    def __init__(
        self,
        screen_width: int,
        screen_height: int,
        screenshot_width: int | None = None,
        screenshot_height: int | None = None,
    ) -> None:
        self.screen = ScreenInfo(
            width=int(screen_width),
            height=int(screen_height),
            screenshot_width=int(screenshot_width or screen_width),
            screenshot_height=int(screenshot_height or screen_height),
        )
        self.regions: dict[str, list[int]] = {}
        self.configs: dict[str, dict[str, Any]] = {}
        self.title_region: str | None = None
        self.desc_region: str | None = None
        self.plan_name: str = "capture-plan"

    # ------------------------------------------------------------------ #
    # 构造 / 导入
    # ------------------------------------------------------------------ #
    @classmethod
    def from_plan(cls, plan: CapturePlan) -> "PlanBuilder":
        builder = cls(
            plan.screen.width,
            plan.screen.height,
            plan.screen.screenshot_width,
            plan.screen.screenshot_height,
        )
        builder.regions = {name: region.as_list() for name, region in plan.regions.items()}
        builder.configs = {name: cfg.to_dict() for name, cfg in plan.region_configs.items()}
        builder.title_region = plan.output.title_region
        builder.desc_region = plan.output.desc_region
        builder.plan_name = plan.name
        return builder

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PlanBuilder":
        return cls.from_plan(plan_from_dict(data))

    # ------------------------------------------------------------------ #
    # 区域操作
    # ------------------------------------------------------------------ #
    def next_name(self) -> str:
        return next_region_name(self.regions)

    def add_region(self, coords: Sequence[Any]) -> str:
        name = self.next_name()
        region = Region.from_list(name, coords)
        region.validate_bounds(self.screen.screenshot_width, self.screen.screenshot_height)
        if region.width < 10 or region.height < 10:
            raise ConfigError(f"框选范围过小（{region.width}x{region.height}），请重新拖拽")
        self.regions[name] = region.as_list()
        self.configs[name] = default_transcribe_config()
        return name

    def remove_region(self, name: str) -> None:
        self.regions.pop(name, None)
        self.configs.pop(name, None)
        if self.title_region == name:
            self.title_region = None
        if self.desc_region == name:
            self.desc_region = None
        for cfg in self.configs.values():
            if cfg.get("mode") == MODE_SMS:
                invoke = [item for item in cfg.get("invoke_regions", []) if item != name]
                cfg["invoke_regions"] = invoke
                if cfg.get("key_region") == name:
                    cfg["key_region"] = None

    def set_region_coords(self, name: str, coords: Sequence[Any]) -> None:
        if name not in self.regions:
            raise ConfigError(f"不存在的区域：{name}")
        region = Region.from_list(name, coords)
        region.validate_bounds(self.screen.screenshot_width, self.screen.screenshot_height)
        self.regions[name] = region.as_list()

    def set_mode(self, name: str, mode: str) -> None:
        if name not in self.regions:
            raise ConfigError(f"不存在的区域：{name}")
        if mode not in (MODE_SMS, MODE_TRANSCRIBE):
            raise ConfigError(f"不支持的区域模式：{mode!r}")
        current = self.configs.get(name, {})
        if current.get("mode") == mode:
            return
        if mode == MODE_SMS:
            self.configs[name] = default_sms_config()
        else:
            self.configs[name] = default_transcribe_config()
            for cfg in self.configs.values():
                if cfg.get("mode") == MODE_SMS and name in cfg.get("invoke_regions", []):
                    cfg["invoke_regions"] = [i for i in cfg["invoke_regions"] if i != name]
                if cfg.get("key_region") == name:
                    cfg["key_region"] = None
            if self.title_region == name:
                self.title_region = None
            if self.desc_region == name:
                self.desc_region = None

    def config(self, name: str) -> dict[str, Any]:
        return self.configs[name]

    def update_config(self, name: str, values: Mapping[str, Any]) -> None:
        if name not in self.configs:
            raise ConfigError(f"不存在的区域：{name}")
        mode = self.configs[name].get("mode")
        allowed = _ALLOWED_SMS_KEYS if mode == MODE_SMS else _ALLOWED_TRANSCRIBE_KEYS
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise ConfigError(f"区域 {name} 的配置包含未知字段：{', '.join(unknown)}")
        if "mode" in values and values["mode"] != mode:
            raise ConfigError("不能通过 update_config 切换模式，请使用 set_mode")
        self.configs[name].update(values)

    def set_output_regions(self, title_region: str | None, desc_region: str | None) -> None:
        self.title_region = title_region or None
        self.desc_region = desc_region or None

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def build(
        self,
        *,
        plan_name: str | None = None,
        max_jobs: int = 10,
        engine: str = "mock",
        llm: str = "mock",
        output_dir: str = "runtime/output",
        mock_scenario: str = "data/samples/demo_story.json",
        minimize_frontend: bool = False,
        window_title: str = "Streamlit",
        start_delay_seconds: int = 5,
        ocr_confidence_threshold: float = 0.8,
        on_job_error: str = "abort",
    ) -> CapturePlan:
        if not self.regions:
            raise ConfigError("尚未框选任何区域")
        regions = {
            name: Region.from_list(name, coords) for name, coords in self.regions.items()
        }
        region_configs = {
            name: (
                SmsConfig.from_dict(cfg)
                if cfg.get("mode") == MODE_SMS
                else TranscribeConfig.from_dict(cfg)
            )
            for name, cfg in self.configs.items()
        }
        run = RunConfig(
            max_jobs=max_jobs,
            engine=engine,
            llm=llm,
            mock_scenario=mock_scenario,
            minimize_frontend=minimize_frontend,
            window_title=window_title,
            start_delay_seconds=start_delay_seconds,
            ocr_confidence_threshold=ocr_confidence_threshold,
            on_job_error=on_job_error,
        )
        output = OutputConfig(
            dir=output_dir,
            title_region=self.title_region,
            desc_region=self.desc_region,
        )
        plan = CapturePlan(
            regions=regions,
            region_configs=region_configs,
            screen=self.screen,
            run=run,
            output=output,
            name=plan_name or self.plan_name,
        )
        plan.validate()
        return plan

    def try_build(self, **kwargs: Any) -> tuple[CapturePlan | None, list[str]]:
        try:
            return self.build(**kwargs), []
        except ConfigError as exc:
            return None, [str(exc)]

    def suggest_output_regions(self) -> tuple[str | None, str | None]:
        """在未显式选择时，用字典模式键区域/列表区域给出建议。"""

        title = self.title_region
        desc = self.desc_region
        for name, cfg in self.configs.items():
            if cfg.get("mode") == MODE_SMS:
                if not title and cfg.get("key_region"):
                    title = cfg["key_region"]
                if not desc:
                    candidates = [
                        item
                        for item in cfg.get("invoke_regions", [])
                        if item != cfg.get("key_region")
                        and self.configs.get(item, {}).get("expected_format", "list") != "str"
                    ]
                    if candidates:
                        desc = candidates[0]
        return title, desc

    def summary_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for name, coords in self.regions.items():
            cfg = self.configs.get(name, {})
            mode = cfg.get("mode", MODE_TRANSCRIBE)
            if mode == MODE_SMS:
                invoke = "、".join(cfg.get("invoke_regions", [])) or "未配置"
                extra = f"调用：{invoke}"
            else:
                scrollable = "可滚动" if cfg.get("scrollable") else "不滚动"
                extra = f"{scrollable}／期望格式：{cfg.get('expected_format')}"
            rows.append(
                {
                    "区域": name,
                    "坐标": coords,
                    "模式": "短信模式" if mode == MODE_SMS else "抄录模式",
                    "要点": extra,
                }
            )
        return rows
