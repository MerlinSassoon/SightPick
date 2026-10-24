"""拟人化输入保护的可配置参数（计划文件中的 ``run.humanize``）。

``HumanProfile`` 是驱动层使用的运行时参数（不可变，区间用二元组表示）；
``HumanizeConfig`` 在其基础上增加 ``enabled`` / ``idle_wander`` 两个运行开关，
并负责 JSON 读写与逐项校验。两者共用同一份字段定义与默认值，避免“代码一套
默认值、配置又一套默认值”。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from typing import Any

from ..errors import ConfigError

# 区间参数：下界/上界都必须 ≥ 0，且下界不得大于上界
_FLOAT_RANGES = (
    "move_duration",
    "curve_bend",
    "overshoot_px",
    "pre_click_pause",
    "action_gap",
    "scroll_interval",
    "idle_after",
    "idle_interval",
    "block_duration",
)
_INT_RANGES = ("settle_times", "scroll_step", "fidget_px", "idle_px")
_SCALARS = ("move_max_duration", "tremor_px", "drift_px")
_PROBABILITIES = ("overshoot_chance", "fidget_chance")


def _require_mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigError(f"{where} 必须是对象，收到：{type(value).__name__}")
    return value


def _unknown_keys(data: Mapping[str, Any], allowed: Sequence[str], where: str) -> None:
    extras = sorted(set(data) - set(allowed))
    if extras:
        raise ConfigError(f"{where} 包含未知配置项：{', '.join(extras)}")


def _number(value: Any, where: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{where} 必须是数字，收到：{value!r}")
    result = float(value)
    if minimum is not None and result < minimum:
        raise ConfigError(f"{where} 不能小于 {minimum:g}，收到：{value!r}")
    return result


def _integer(value: Any, where: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool):
        raise ConfigError(f"{where} 必须是整数，收到：{value!r}")
    if isinstance(value, float) and value.is_integer():
        value = int(value)  # 兼容 JSON 里的 2.0 这类写法
    if not isinstance(value, int):
        raise ConfigError(f"{where} 必须是整数，收到：{value!r}")
    if minimum is not None and value < minimum:
        raise ConfigError(f"{where} 不能小于 {minimum}，收到：{value!r}")
    return value


def _bool(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{where} 必须是布尔值，收到：{value!r}")
    return value


def _range(
    value: Any,
    where: str,
    *,
    integer: bool,
    minimum: float | int,
) -> tuple[float | int, float | int]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != 2:
        raise ConfigError(f"{where} 必须是 [下界, 上界] 二元组，收到：{value!r}")
    converter = _integer if integer else _number
    low = converter(value[0], f"{where}[0]", minimum=minimum)
    high = converter(value[1], f"{where}[1]", minimum=minimum)
    if low > high:
        raise ConfigError(f"{where} 的下界不能大于上界，收到：{value!r}")
    return (low, high)


def _probability(value: Any, where: str) -> float:
    result = _number(value, where, minimum=0.0)
    if result > 1.0:
        raise ConfigError(f"{where} 必须在 0~1 之间，收到：{value!r}")
    return result


@dataclass(frozen=True)
class HumanProfile:
    """拟人化行为参数（驱动层运行时对象）。

    区间参数统一写成 ``(下界, 上界)``；构造时逐项校验，非法值抛
    :class:`ConfigError`，避免把错误配置带到真实鼠标操作里。
    """

    # —— 移动（贝塞尔曲线 + 手抖）——
    move_duration: tuple[float, float] = (0.18, 0.55)
    """单次移动耗时区间（秒）；调用方传入 ≥0.15s 的 ``duration`` 时作为下限。"""

    move_max_duration: float = 1.20
    """单次移动耗时上限（秒）。"""

    curve_bend: tuple[float, float] = (0.06, 0.20)
    """贝塞尔控制点横向弯曲幅度（相对移动距离的比例）。"""

    tremor_px: float = 0.8
    """移动过程中的单步高斯手抖标准差（像素）。"""

    settle_times: tuple[int, int] = (1, 3)
    """到达后落点微调次数区间。"""

    overshoot_px: tuple[float, float] = (10.0, 110.0)
    """过冲距离区间（像素）。"""

    overshoot_chance: float = 0.85
    """触发过冲回拉的概率（0~1）。"""

    drift_px: float = 1.5
    """终点误差漂移半径（像素），0 表示不漂移。"""

    # —— 停顿 ——
    pre_click_pause: tuple[float, float] = (0.08, 0.28)
    """移动结束到按下之间的确认停顿区间（秒）。"""

    action_gap: tuple[float, float] = (0.08, 0.30)
    """两个动作之间的自然间隔区间（秒）。"""

    # —— 滚动 ——
    scroll_step: tuple[int, int] = (110, 130)
    """滚动分步的每步格数区间。"""

    scroll_interval: tuple[float, float] = (0.03, 0.12)
    """滚动步与步之间的间隔区间（秒）。"""

    scroll_overshoot: bool = True
    """是否启用滚动过冲回拉。"""

    # —— 无意义鼠标晃动 ——
    fidget_chance: float = 0.15
    """动作间隙触发无意义晃动的概率（0~1）。"""

    fidget_px: tuple[int, int] = (1, 4)
    """无意义晃动的单次偏移幅度区间（像素）。"""

    # —— 空闲无意识晃动 ——
    idle_after: tuple[float, float] = (8.0, 20.0)
    """判定“无操作”所需的空闲时长区间（秒）。"""

    idle_interval: tuple[float, float] = (1.0, 4.0)
    """空闲状态下两次微动的间隔区间（秒）。"""

    idle_px: tuple[int, int] = (1, 2)
    """空闲微动的偏移幅度区间（像素）。"""

    # —— 可阻塞工作流程 ——
    block_every: tuple[int, int] | None = (12, 30)
    """每多少次动作插入一次阻塞停顿；``None`` 表示不插入。"""

    block_duration: tuple[float, float] = (1.5, 5.0)
    """阻塞停顿时长区间（秒）。"""

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """逐项校验；``from_dict`` 与直接构造都会走到这里。"""

        for name in _FLOAT_RANGES:
            _range(getattr(self, name), f"run.humanize.{name}", integer=False, minimum=0.0)
        for name in _INT_RANGES:
            _range(getattr(self, name), f"run.humanize.{name}", integer=True, minimum=0)
        for name in _SCALARS:
            _number(getattr(self, name), f"run.humanize.{name}", minimum=0.0)
        for name in _PROBABILITIES:
            _probability(getattr(self, name), f"run.humanize.{name}")
        if self.block_every is not None:
            _range(self.block_every, "run.humanize.block_every", integer=True, minimum=1)
        _bool(self.scroll_overshoot, "run.humanize.scroll_overshoot")


#: :class:`HumanProfile` 的全部字段名，供配置层逐项映射。
PROFILE_FIELDS = tuple(field.name for field in fields(HumanProfile))


@dataclass(frozen=True)
class HumanizeConfig(HumanProfile):
    """计划文件 ``run.humanize``：算法参数 + 总开关 + 空闲微动开关。"""

    enabled: bool = True
    """总开关；关闭后驱动不做任何拟人化处理。"""

    idle_wander: bool = True
    """是否在长时间无操作时启动后台微动线程。"""

    # 开关放在最前面，便于阅读 JSON；不参与 dataclass 字段计算
    FIELDS = ("enabled", "idle_wander", *PROFILE_FIELDS)

    def validate(self) -> None:
        super().validate()
        _bool(self.enabled, "run.humanize.enabled")
        _bool(self.idle_wander, "run.humanize.idle_wander")

    def to_profile(self) -> HumanProfile:
        """取出纯运行时参数（丢弃两个开关）。"""

        return HumanProfile(**{name: getattr(self, name) for name in PROFILE_FIELDS})

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "HumanizeConfig":
        raw = _require_mapping({} if data is None else data, "run.humanize")
        _unknown_keys(raw, cls.FIELDS, "run.humanize")
        values: dict[str, Any] = {
            "enabled": _bool(raw.get("enabled", _DEFAULTS.enabled), "run.humanize.enabled"),
            "idle_wander": _bool(
                raw.get("idle_wander", _DEFAULTS.idle_wander),
                "run.humanize.idle_wander",
            ),
        }
        for name in PROFILE_FIELDS:
            label = f"run.humanize.{name}"
            default = getattr(_DEFAULTS, name)
            value = raw.get(name, default)
            if name in _FLOAT_RANGES:
                values[name] = _range(value, label, integer=False, minimum=0.0)
            elif name in _INT_RANGES:
                values[name] = _range(value, label, integer=True, minimum=0)
            elif name in _SCALARS:
                values[name] = _number(value, label, minimum=0.0)
            elif name in _PROBABILITIES:
                values[name] = _probability(value, label)
            elif name == "block_every":
                values[name] = (
                    None
                    if value is None
                    else _range(value, label, integer=True, minimum=1)
                )
            elif name == "scroll_overshoot":
                values[name] = _bool(value, label)
            else:  # pragma: no cover - 新增字段时忘记登记分类
                raise ConfigError(f"run.humanize.{name} 未登记校验规则")
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        for name in self.FIELDS:
            value = getattr(self, name)
            data[name] = list(value) if isinstance(value, tuple) else value
        return data


_DEFAULTS = HumanizeConfig()
"""默认值基准：``from_dict`` 缺省时从这里取，保证与代码默认值一致。"""
