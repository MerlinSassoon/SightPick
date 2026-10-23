"""计划、区域配置与运行配置的数据模型（NEWREADME 5.3 / 6.x）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ..errors import ConfigError
from .region import Region, ScreenGeometry

SCHEMA_VERSION = "1.0"

MODE_SMS = "sms"
MODE_TRANSCRIBE = "transcribe"
MODES = (MODE_SMS, MODE_TRANSCRIBE)

FORMAT_STR = "str"
FORMAT_LIST = "list"
FORMAT_TUPLE = "tuple"
FORMATS = (FORMAT_STR, FORMAT_LIST, FORMAT_TUPLE)

DATA_STRUCTURE_SET = "set"
DATA_STRUCTURE_DICT = "dict"
DATA_STRUCTURES = (DATA_STRUCTURE_SET, DATA_STRUCTURE_DICT)

STOP_MATCH_SUBSTRING = "substring"
STOP_MATCH_EXACT = "exact"
STOP_MATCH_MODES = (STOP_MATCH_SUBSTRING, STOP_MATCH_EXACT)

ENGINES = ("real", "mock")
LLM_OPTIONS = ("deepseek", "mock")
ON_JOB_ERROR = ("abort", "skip")
ON_SCREEN_CHANGE = ("abort", "warn")


def _require_mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigError(f"{field_name} 必须是对象，收到：{type(value).__name__}")
    return value


def _unknown_keys(data: Mapping[str, Any], allowed: Sequence[str], where: str) -> None:
    extras = sorted(set(data) - set(allowed))
    if extras:
        raise ConfigError(f"{where} 包含未知配置项：{', '.join(extras)}")


def _as_bool(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ConfigError(f"{field_name} 必须是布尔值，收到：{value!r}")


def _as_int(value: Any, field_name: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        else:
            raise ConfigError(f"{field_name} 必须是整数，收到：{value!r}")
    if minimum is not None and value < minimum:
        raise ConfigError(f"{field_name} 不能小于 {minimum}，收到：{value}")
    return value


def _as_str(value: Any, field_name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{field_name} 必须是字符串，收到：{value!r}")
    if not allow_empty and not value.strip():
        raise ConfigError(f"{field_name} 不能为空")
    return value.strip()


def _as_float(value: Any, field_name: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{field_name} 必须是数字，收到：{value!r}")
    result = float(value)
    if minimum is not None and result < minimum:
        raise ConfigError(f"{field_name} 不能小于 {minimum}，收到：{value}")
    return result


def _as_str_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise ConfigError(f"{field_name} 必须是字符串列表，收到：{value!r}")
    result: list[str] = []
    for index, item in enumerate(value):
        result.append(_as_str(item, f"{field_name}[{index}]"))
    return result


def _as_point(value: Any, field_name: str) -> list[int] | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ConfigError(f"{field_name} 必须是 [x, y] 二元组，收到：{value!r}")
    return [_as_int(value[0], f"{field_name}[0]"), _as_int(value[1], f"{field_name}[1]")]


def _as_enum(value: Any, allowed: Sequence[str], field_name: str, default: str | None = None) -> str:
    if value is None:
        if default is not None:
            return default
        raise ConfigError(f"{field_name} 不能为空")
    if value not in allowed:
        raise ConfigError(f"{field_name} 必须是 {allowed} 之一，收到：{value!r}")
    return value


@dataclass
class TranscribeConfig:
    """抄录模式配置（5.3.2）。"""

    mode: str = MODE_TRANSCRIBE
    scrollable: bool = False
    scroll_distance: int | None = None
    scroll_start: list[int] | None = None
    scroll_stop_markers: list[str] = field(default_factory=list)
    scroll_stop_match: str = STOP_MATCH_SUBSTRING
    expected_content: str = ""
    expected_format: str = FORMAT_LIST
    anchor_key: str | None = None
    max_rounds: int = 12
    max_stale_rounds: int = 2
    denoise_per_round: bool = False
    auto_adjust_scroll: bool = False

    FIELDS = (
        "mode",
        "scrollable",
        "scroll_distance",
        "scroll_start",
        "scroll_stop_markers",
        "scroll_stop_match",
        "expected_content",
        "expected_format",
        "anchor_key",
        "max_rounds",
        "max_stale_rounds",
        "denoise_per_round",
        "auto_adjust_scroll",
    )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TranscribeConfig":
        data = _require_mapping(data, "抄录模式配置")
        _unknown_keys(data, cls.FIELDS, "抄录模式配置")
        mode = _as_enum(data.get("mode", MODE_TRANSCRIBE), MODES, "mode")
        if mode != MODE_TRANSCRIBE:
            raise ConfigError(f"抄录模式配置的 mode 必须为 '{MODE_TRANSCRIBE}'，收到：{mode!r}")
        anchor = data.get("anchor_key")
        config = cls(
            mode=MODE_TRANSCRIBE,
            scrollable=_as_bool(data.get("scrollable", False), "scrollable"),
            scroll_distance=(
                _as_int(data["scroll_distance"], "scroll_distance")
                if data.get("scroll_distance") is not None
                else None
            ),
            scroll_start=_as_point(data.get("scroll_start"), "scroll_start"),
            scroll_stop_markers=_as_str_list(data.get("scroll_stop_markers"), "scroll_stop_markers"),
            scroll_stop_match=_as_enum(
                data.get("scroll_stop_match", STOP_MATCH_SUBSTRING),
                STOP_MATCH_MODES,
                "scroll_stop_match",
            ),
            expected_content=_as_str(
                data.get("expected_content", ""), "expected_content", allow_empty=True
            ),
            expected_format=_as_enum(
                data.get("expected_format", FORMAT_LIST), FORMATS, "expected_format"
            ),
            anchor_key=_as_str(anchor, "anchor_key") if anchor else None,
            max_rounds=_as_int(data.get("max_rounds", 12), "max_rounds", minimum=1),
            max_stale_rounds=_as_int(
                data.get("max_stale_rounds", 2), "max_stale_rounds", minimum=0
            ),
            denoise_per_round=_as_bool(
                data.get("denoise_per_round", False), "denoise_per_round"
            ),
            auto_adjust_scroll=_as_bool(
                data.get("auto_adjust_scroll", False), "auto_adjust_scroll"
            ),
        )
        config.validate("抄录区域")
        return config

    def validate(self, where: str = "抄录区域") -> None:
        if self.expected_format not in FORMATS:
            raise ConfigError(f"{where} 的 expected_format 非法：{self.expected_format!r}")
        if self.scrollable:
            if self.scroll_distance is None or self.scroll_distance == 0:
                raise ConfigError(f"{where} 可滚动时必须配置非 0 的 scroll_distance")
            if self.scroll_start is None:
                raise ConfigError(f"{where} 可滚动时必须配置 scroll_start")
            if not self.scroll_stop_markers:
                raise ConfigError(f"{where} 可滚动时必须配置至少一个滚动停止条件")
            if self.scroll_start[0] < 0 or self.scroll_start[1] < 0:
                raise ConfigError(f"{where} 的 scroll_start 不能为负")

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "scrollable": self.scrollable,
            "scroll_distance": self.scroll_distance,
            "scroll_start": list(self.scroll_start) if self.scroll_start else None,
            "scroll_stop_markers": list(self.scroll_stop_markers),
            "scroll_stop_match": self.scroll_stop_match,
            "expected_content": self.expected_content,
            "expected_format": self.expected_format,
            "anchor_key": self.anchor_key,
            "max_rounds": self.max_rounds,
            "max_stale_rounds": self.max_stale_rounds,
            "denoise_per_round": self.denoise_per_round,
            "auto_adjust_scroll": self.auto_adjust_scroll,
        }


@dataclass
class SmsConfig:
    """短信模式配置（5.3.2）。"""

    mode: str = MODE_SMS
    scroll_distance: int | None = None
    scroll_start: list[int] | None = None
    scroll_stop_markers: list[str] = field(default_factory=list)
    scroll_stop_match: str = STOP_MATCH_SUBSTRING
    invoke_regions: list[str] = field(default_factory=list)
    data_structure: str = DATA_STRUCTURE_DICT
    key_region: str | None = None
    max_items: int | None = None
    verify_click: bool = False
    rollback_step: int = 50
    max_click_retries: int = 3
    click_anchor: list[int] | None = None
    max_rounds: int = 30

    FIELDS = (
        "mode",
        "scroll_distance",
        "scroll_start",
        "scroll_stop_markers",
        "scroll_stop_match",
        "invoke_regions",
        "data_structure",
        "key_region",
        "max_items",
        "verify_click",
        "rollback_step",
        "max_click_retries",
        "click_anchor",
        "max_rounds",
    )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SmsConfig":
        data = _require_mapping(data, "短信模式配置")
        _unknown_keys(data, cls.FIELDS, "短信模式配置")
        mode = _as_enum(data.get("mode", MODE_SMS), MODES, "mode")
        if mode != MODE_SMS:
            raise ConfigError(f"短信模式配置的 mode 必须为 '{MODE_SMS}'，收到：{mode!r}")
        config = cls(
            mode=MODE_SMS,
            scroll_distance=(
                _as_int(data["scroll_distance"], "scroll_distance")
                if data.get("scroll_distance") is not None
                else None
            ),
            scroll_start=_as_point(data.get("scroll_start"), "scroll_start"),
            scroll_stop_markers=_as_str_list(data.get("scroll_stop_markers"), "scroll_stop_markers"),
            scroll_stop_match=_as_enum(
                data.get("scroll_stop_match", STOP_MATCH_SUBSTRING),
                STOP_MATCH_MODES,
                "scroll_stop_match",
            ),
            invoke_regions=_as_str_list(data.get("invoke_regions"), "invoke_regions"),
            data_structure=_as_enum(
                data.get("data_structure", DATA_STRUCTURE_DICT),
                DATA_STRUCTURES,
                "data_structure",
            ),
            key_region=(
                _as_str(data["key_region"], "key_region")
                if data.get("key_region") is not None
                else None
            ),
            max_items=(
                _as_int(data["max_items"], "max_items", minimum=1)
                if data.get("max_items") is not None
                else None
            ),
            verify_click=_as_bool(data.get("verify_click", False), "verify_click"),
            rollback_step=_as_int(
                data.get("rollback_step", 50), "rollback_step", minimum=1
            ),
            max_click_retries=_as_int(
                data.get("max_click_retries", 3), "max_click_retries", minimum=1
            ),
            click_anchor=_as_point(data.get("click_anchor"), "click_anchor"),
            max_rounds=_as_int(data.get("max_rounds", 30), "max_rounds", minimum=1),
        )
        config.validate("短信区域")
        return config

    def validate(self, where: str = "短信区域") -> None:
        if self.scroll_distance is None or self.scroll_distance == 0:
            raise ConfigError(f"{where} 必须配置非 0 的 scroll_distance")
        if self.scroll_start is None:
            raise ConfigError(f"{where} 必须配置 scroll_start")
        if not self.scroll_stop_markers:
            raise ConfigError(f"{where} 必须配置至少一个滚动停止条件")
        if not self.invoke_regions:
            raise ConfigError(f"{where} 必须至少调用一个抄录模式区域")
        if len(set(self.invoke_regions)) != len(self.invoke_regions):
            raise ConfigError(f"{where} 的 invoke_regions 不能重复")
        if self.data_structure == DATA_STRUCTURE_DICT:
            if not self.key_region:
                raise ConfigError(f"{where} 为字典模式时必须指定 key_region")
            if self.key_region not in self.invoke_regions:
                raise ConfigError(
                    f"{where} 的 key_region（{self.key_region}）必须在 invoke_regions 中"
                )
        elif self.key_region:
            raise ConfigError(f"{where} 为集合模式时不应配置 key_region")

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "scroll_distance": self.scroll_distance,
            "scroll_start": list(self.scroll_start) if self.scroll_start else None,
            "scroll_stop_markers": list(self.scroll_stop_markers),
            "scroll_stop_match": self.scroll_stop_match,
            "invoke_regions": list(self.invoke_regions),
            "data_structure": self.data_structure,
            "key_region": self.key_region,
            "max_items": self.max_items,
            "verify_click": self.verify_click,
            "rollback_step": self.rollback_step,
            "max_click_retries": self.max_click_retries,
            "click_anchor": list(self.click_anchor) if self.click_anchor else None,
            "max_rounds": self.max_rounds,
        }


RegionConfig = TranscribeConfig | SmsConfig


def config_from_dict(data: Mapping[str, Any]) -> RegionConfig:
    data = _require_mapping(data, "区域配置")
    mode = data.get("mode")
    if mode == MODE_SMS:
        return SmsConfig.from_dict(data)
    if mode == MODE_TRANSCRIBE:
        return TranscribeConfig.from_dict(data)
    raise ConfigError(f"区域配置的 mode 必须是 {MODES} 之一，收到：{mode!r}")


def default_sms_config() -> dict[str, Any]:
    return SmsConfig(
        scroll_distance=-180,
        scroll_start=[400, 500],
        scroll_stop_markers=["企业服务", "使用与帮助"],
        invoke_regions=[],
        data_structure=DATA_STRUCTURE_DICT,
        key_region=None,
    ).to_dict()


def default_transcribe_config() -> dict[str, Any]:
    return TranscribeConfig(
        scrollable=False,
        expected_content="",
        expected_format=FORMAT_LIST,
    ).to_dict()


@dataclass
class ScreenInfo:
    width: int
    height: int
    screenshot_width: int
    screenshot_height: int

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ScreenInfo":
        data = _require_mapping(data, "screen")
        _unknown_keys(data, ("width", "height", "screenshot_width", "screenshot_height"), "screen")
        return cls(
            width=_as_int(data.get("width"), "screen.width", minimum=1),
            height=_as_int(data.get("height"), "screen.height", minimum=1),
            screenshot_width=_as_int(
                data.get("screenshot_width", data.get("width")),
                "screen.screenshot_width",
                minimum=1,
            ),
            screenshot_height=_as_int(
                data.get("screenshot_height", data.get("height")),
                "screen.screenshot_height",
                minimum=1,
            ),
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "width": self.width,
            "height": self.height,
            "screenshot_width": self.screenshot_width,
            "screenshot_height": self.screenshot_height,
        }

    def geometry(self) -> ScreenGeometry:
        return ScreenGeometry(
            screen_width=self.width,
            screen_height=self.height,
            shot_width=self.screenshot_width,
            shot_height=self.screenshot_height,
        )


@dataclass
class RunConfig:
    """运行参数（NEWREADME 未定义的部分，见 docs/AMBIGUITIES.md）。"""

    max_jobs: int = 10
    engine: str = "mock"
    llm: str = "mock"
    mock_scenario: str = "data/samples/demo_story.json"
    llm_model: str = "deepseek-flash"
    llm_base_url: str = "https://api.deepseek.com"
    llm_timeout_seconds: int = 60
    llm_max_retries: int = 1
    llm_async: bool = True
    llm_worker_threads: int = 2
    llm_queue_max: int = 8
    llm_task_retries: int = 1
    llm_task_timeout_seconds: int = 90
    llm_drain_timeout_seconds: int = 30
    minimize_frontend: bool = False
    window_title: str = "Streamlit"
    start_delay_seconds: int = 5
    ocr_confidence_threshold: float = 0.8
    click_pause_seconds: float = 3.0
    scroll_pause_seconds: float = 1.2
    min_action_interval_seconds: float = 0.1
    max_action_interval_seconds: float = 0.4
    on_job_error: str = "abort"
    on_screen_change: str = "abort"
    save_screenshots: bool = False
    px_per_scroll_unit: float = 1.0

    FIELDS = (
        "max_jobs",
        "engine",
        "llm",
        "mock_scenario",
        "llm_model",
        "llm_base_url",
        "llm_timeout_seconds",
        "llm_max_retries",
        "llm_async",
        "llm_worker_threads",
        "llm_queue_max",
        "llm_task_retries",
        "llm_task_timeout_seconds",
        "llm_drain_timeout_seconds",
        "minimize_frontend",
        "window_title",
        "start_delay_seconds",
        "ocr_confidence_threshold",
        "click_pause_seconds",
        "scroll_pause_seconds",
        "min_action_interval_seconds",
        "max_action_interval_seconds",
        "on_job_error",
        "on_screen_change",
        "save_screenshots",
        "px_per_scroll_unit",
    )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "RunConfig":
        data = _require_mapping(data or {}, "run")
        _unknown_keys(data, cls.FIELDS, "run")
        threshold = data.get("ocr_confidence_threshold", 0.8)
        if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
            raise ConfigError(f"ocr_confidence_threshold 必须是数字，收到：{threshold!r}")
        if not 0 <= float(threshold) <= 1:
            raise ConfigError(f"ocr_confidence_threshold 必须在 0~1 之间，收到：{threshold!r}")
        return cls(
            max_jobs=_as_int(data.get("max_jobs", 10), "max_jobs", minimum=1),
            engine=_as_enum(data.get("engine", "mock"), ENGINES, "engine"),
            llm=_as_enum(data.get("llm", "mock"), LLM_OPTIONS, "llm"),
            mock_scenario=_as_str(
                data.get("mock_scenario", "data/samples/demo_story.json"), "mock_scenario"
            ),
            llm_model=_as_str(data.get("llm_model", "deepseek-flash"), "llm_model"),
            llm_base_url=_as_str(
                data.get("llm_base_url", "https://api.deepseek.com"), "llm_base_url"
            ),
            llm_timeout_seconds=_as_int(
                data.get("llm_timeout_seconds", 60), "llm_timeout_seconds", minimum=1
            ),
            llm_max_retries=_as_int(
                data.get("llm_max_retries", 1), "llm_max_retries", minimum=0
            ),
            llm_async=_as_bool(data.get("llm_async", True), "llm_async"),
            llm_worker_threads=_as_int(
                data.get("llm_worker_threads", 2), "llm_worker_threads", minimum=1
            ),
            llm_queue_max=_as_int(
                data.get("llm_queue_max", 8), "llm_queue_max", minimum=1
            ),
            llm_task_retries=_as_int(
                data.get("llm_task_retries", 1), "llm_task_retries", minimum=0
            ),
            llm_task_timeout_seconds=_as_int(
                data.get("llm_task_timeout_seconds", 90),
                "llm_task_timeout_seconds",
                minimum=1,
            ),
            llm_drain_timeout_seconds=_as_int(
                data.get("llm_drain_timeout_seconds", 30),
                "llm_drain_timeout_seconds",
                minimum=0,
            ),
            minimize_frontend=_as_bool(
                data.get("minimize_frontend", False), "minimize_frontend"
            ),
            window_title=_as_str(data.get("window_title", "Streamlit"), "window_title"),
            start_delay_seconds=_as_int(
                data.get("start_delay_seconds", 5), "start_delay_seconds", minimum=0
            ),
            ocr_confidence_threshold=float(threshold),
            click_pause_seconds=_as_float(
                data.get("click_pause_seconds", 3.0), "click_pause_seconds", minimum=0
            ),
            scroll_pause_seconds=_as_float(
                data.get("scroll_pause_seconds", 1.2), "scroll_pause_seconds", minimum=0
            ),
            min_action_interval_seconds=_as_float(
                data.get("min_action_interval_seconds", 0.1),
                "min_action_interval_seconds",
                minimum=0,
            ),
            max_action_interval_seconds=_as_float(
                data.get("max_action_interval_seconds", 0.4),
                "max_action_interval_seconds",
                minimum=0,
            ),
            on_job_error=_as_enum(
                data.get("on_job_error", "abort"), ON_JOB_ERROR, "on_job_error"
            ),
            on_screen_change=_as_enum(
                data.get("on_screen_change", "abort"), ON_SCREEN_CHANGE, "on_screen_change"
            ),
            save_screenshots=_as_bool(data.get("save_screenshots", False), "save_screenshots"),
            px_per_scroll_unit=_as_float(
                data.get("px_per_scroll_unit", 1.0), "px_per_scroll_unit", minimum=1e-6
            ),
        )

    def validate(self) -> None:
        if self.max_jobs < 1:
            raise ConfigError("run.max_jobs 必须 >= 1")
        if self.click_pause_seconds < 0 or self.scroll_pause_seconds < 0:
            raise ConfigError("点击/滚动等待时间不能为负")
        if self.min_action_interval_seconds < 0 or self.max_action_interval_seconds < 0:
            raise ConfigError("动作间隔不能为负")
        if self.min_action_interval_seconds > self.max_action_interval_seconds:
            raise ConfigError("min_action_interval_seconds 不能大于 max_action_interval_seconds")
        if self.px_per_scroll_unit <= 0:
            raise ConfigError("px_per_scroll_unit 必须大于 0")
        # 仅记录，不 raise：既有计划与测试会在不改 run 配置的情况下调用 validate()
        worst = self.llm_timeout_seconds * (self.llm_max_retries + 1)
        self._timeout_warning = ""
        if self.llm_async and self.llm_task_timeout_seconds > worst:
            self._timeout_warning = (
                f"llm_task_timeout_seconds({self.llm_task_timeout_seconds}) 大于单任务最坏耗时"
                f"({worst}s)：主线程可能先放弃等待而 worker 稍后完成（结果会被幂等忽略）"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_jobs": self.max_jobs,
            "engine": self.engine,
            "llm": self.llm,
            "mock_scenario": self.mock_scenario,
            "llm_model": self.llm_model,
            "llm_base_url": self.llm_base_url,
            "llm_timeout_seconds": self.llm_timeout_seconds,
            "llm_max_retries": self.llm_max_retries,
            "llm_async": self.llm_async,
            "llm_worker_threads": self.llm_worker_threads,
            "llm_queue_max": self.llm_queue_max,
            "llm_task_retries": self.llm_task_retries,
            "llm_task_timeout_seconds": self.llm_task_timeout_seconds,
            "llm_drain_timeout_seconds": self.llm_drain_timeout_seconds,
            "minimize_frontend": self.minimize_frontend,
            "window_title": self.window_title,
            "start_delay_seconds": self.start_delay_seconds,
            "ocr_confidence_threshold": self.ocr_confidence_threshold,
            "click_pause_seconds": self.click_pause_seconds,
            "scroll_pause_seconds": self.scroll_pause_seconds,
            "min_action_interval_seconds": self.min_action_interval_seconds,
            "max_action_interval_seconds": self.max_action_interval_seconds,
            "on_job_error": self.on_job_error,
            "on_screen_change": self.on_screen_change,
            "save_screenshots": self.save_screenshots,
            "px_per_scroll_unit": self.px_per_scroll_unit,
        }


@dataclass
class OutputConfig:
    dir: str = "runtime/output"
    formats: list[str] = field(default_factory=lambda: ["jsonl", "json"])
    title_region: str | None = None
    desc_region: str | None = None
    extra_fields: dict[str, str] = field(default_factory=dict)

    FIELDS = ("dir", "formats", "title_region", "desc_region", "extra_fields")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "OutputConfig":
        data = _require_mapping(data or {}, "output")
        _unknown_keys(data, cls.FIELDS, "output")
        formats = data.get("formats", ["jsonl", "json"])
        if not isinstance(formats, (list, tuple)):
            raise ConfigError("output.formats 必须是列表")
        formats = [str(item) for item in formats]
        if not formats:
            raise ConfigError("output.formats 至少需要一个格式")
        for item in formats:
            if item not in ("jsonl", "json"):
                raise ConfigError(f"output.formats 仅支持 jsonl/json，收到：{item!r}")
        extra = data.get("extra_fields") or {}
        if not isinstance(extra, Mapping):
            raise ConfigError("output.extra_fields 必须是对象")
        for key, value in extra.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise ConfigError("output.extra_fields 的键和值都必须是字符串")
        title_region = data.get("title_region")
        desc_region = data.get("desc_region")
        return cls(
            dir=_as_str(data.get("dir", "runtime/output"), "output.dir"),
            formats=formats,
            title_region=_as_str(title_region, "output.title_region") if title_region else None,
            desc_region=_as_str(desc_region, "output.desc_region") if desc_region else None,
            extra_fields=dict(extra),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "dir": self.dir,
            "formats": list(self.formats),
            "title_region": self.title_region,
            "desc_region": self.desc_region,
            "extra_fields": dict(self.extra_fields),
        }


@dataclass
class CapturePlan:
    """完整采集计划：区域字典 + 区域配置字典 + 运行/输出参数。"""

    regions: dict[str, Region]
    region_configs: dict[str, RegionConfig]
    screen: ScreenInfo
    run: RunConfig = field(default_factory=RunConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    name: str = "capture-plan"
    schema_version: str = SCHEMA_VERSION

    @property
    def geometry(self) -> ScreenGeometry:
        return self.screen.geometry()

    def sms_regions(self) -> list[str]:
        return [
            name for name, cfg in self.region_configs.items() if isinstance(cfg, SmsConfig)
        ]

    def transcribe_regions(self) -> list[str]:
        return [
            name
            for name, cfg in self.region_configs.items()
            if isinstance(cfg, TranscribeConfig)
        ]

    def validate(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ConfigError(
                f"不支持的 schema_version：{self.schema_version!r}（当前支持 {SCHEMA_VERSION}）"
            )
        if not self.regions:
            raise ConfigError("计划中没有任何区域")
        if set(self.regions) != set(self.region_configs):
            missing = sorted(set(self.regions) - set(self.region_configs))
            extra = sorted(set(self.region_configs) - set(self.regions))
            raise ConfigError(
                "区域字典与区域配置字典的键不一致："
                f"缺少配置={missing or '无'}，多余配置={extra or '无'}"
            )
        for name, region in self.regions.items():
            region.validate_bounds(self.screen.screenshot_width, self.screen.screenshot_height)
            config = self.region_configs[name]
            if isinstance(config, TranscribeConfig):
                if region.width < 20 or region.height < 20:
                    raise ConfigError(f"抄录区域 {name} 过小（至少 20x20），请重新框选")

        for name, config in self.region_configs.items():
            if isinstance(config, SmsConfig):
                for invoke in config.invoke_regions:
                    if invoke not in self.region_configs:
                        raise ConfigError(
                            f"短信区域 {name} 引用了不存在的区域 {invoke}"
                        )
                    if not isinstance(self.region_configs[invoke], TranscribeConfig):
                        raise ConfigError(
                            f"短信区域 {name} 的 invoke_regions 中 {invoke} 不是抄录模式区域"
                        )
                if config.data_structure == DATA_STRUCTURE_DICT and config.key_region:
                    key_cfg = self.region_configs.get(config.key_region)
                    if not isinstance(key_cfg, TranscribeConfig):
                        raise ConfigError(
                            f"短信区域 {name} 的 key_region（{config.key_region}）不是抄录模式区域"
                        )
                    if key_cfg.expected_format != FORMAT_STR:
                        raise ConfigError(
                            f"字典模式下做键的抄录区域 {config.key_region} 的 expected_format "
                            f"必须为 '{FORMAT_STR}'（结果需可哈希），当前为 {key_cfg.expected_format!r}"
                        )

        if self.output.title_region:
            if self.output.title_region not in self.region_configs:
                raise ConfigError(f"output.title_region 指向不存在的区域 {self.output.title_region}")
            if not isinstance(self.region_configs[self.output.title_region], TranscribeConfig):
                raise ConfigError("output.title_region 必须是抄录模式区域")
        if self.output.desc_region:
            if self.output.desc_region not in self.region_configs:
                raise ConfigError(f"output.desc_region 指向不存在的区域 {self.output.desc_region}")
            if not isinstance(self.region_configs[self.output.desc_region], TranscribeConfig):
                raise ConfigError("output.desc_region 必须是抄录模式区域")
            if self.region_configs[self.output.desc_region].expected_format == FORMAT_STR:
                raise ConfigError("output.desc_region 的 expected_format 不应为 str")
        if self.output.title_region and not any(
            isinstance(cfg, SmsConfig) and self.output.title_region in cfg.invoke_regions
            for cfg in self.region_configs.values()
        ):
            raise ConfigError(
                f"output.title_region（{self.output.title_region}）未被任何短信区域调用"
            )
        if self.output.desc_region and not any(
            isinstance(cfg, SmsConfig) and self.output.desc_region in cfg.invoke_regions
            for cfg in self.region_configs.values()
        ):
            raise ConfigError(
                f"output.desc_region（{self.output.desc_region}）未被任何短信区域调用"
            )
        self.run.validate()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "screen": self.screen.to_dict(),
            "regions": {name: region.as_list() for name, region in self.regions.items()},
            "region_configs": {
                name: cfg.to_dict() for name, cfg in self.region_configs.items()
            },
            "output": self.output.to_dict(),
            "run": self.run.to_dict(),
        }
