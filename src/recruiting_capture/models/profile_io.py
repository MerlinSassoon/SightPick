"""配置档案（Profile）的读写与跨分辨率换算。

设计要点
--------
1. **档案与计划同构**：档案就是一份去掉了 ``run`` 的计划。因此加载时直接交给
   :func:`recruiting_capture.models.plan_io.plan_from_dict`，免费获得
   ``Region.from_list`` / ``SmsConfig.from_dict`` / ``TranscribeConfig.from_dict``
   / ``CapturePlan.validate`` 的全部校验，不在本模块重复实现。
2. **只保存可复用的静态配置**：区域坐标、区域配置、输出映射、屏幕几何。
   运行参数（引擎/大模型/份数/等待时长……）属于单次运行，不进档案。
3. **坐标基准是截图像素**：分辨率是否一致要比 ``screenshot_width/height``，
   而不是 ``screen.width/height``；``validate_bounds`` 用的正是前者。
4. **缩放的尺度严格区分**：区域坐标、``scroll_start``、``click_anchor`` 是像素，
   按比例缩放；``scroll_distance`` 是滚轮单位（见 ``RunConfig.px_per_scroll_unit``），
   **绝不缩放**。

格式（``kind="profile"``）::

    {
      "kind": "profile",
      "schema_version": "1.0",
      "name": "boss_1080p",
      "created_at": "2026-10-23T10:12:00+08:00",
      "screen": {"width": 1920, "height": 1080,
                 "screenshot_width": 1920, "screenshot_height": 1080},
      "regions": {"A": [212, 300, 620, 930]},
      "region_configs": {"A": {"mode": "sms", "...": "..."}},
      "output": {"title_region": "C", "desc_region": "B"}
    }
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Mapping

from ..errors import ConfigError
from .config import (
    CapturePlan,
    ScreenInfo,
    SmsConfig,
    TranscribeConfig,
)
from .plan_io import plan_from_dict
from .region import Region

PROFILE_KIND = "profile"
PROFILE_SUFFIX = ".json"
PROFILE_META_FIELDS = ("kind", "created_at")
PROFILE_OUTPUT_FIELDS = ("title_region", "desc_region")

#: ``CapturePlan.validate`` 对抄录区域的最小尺寸要求（见 config.py）。
MIN_TRANSCRIBE_SIZE = 20

MinSizePolicy = Literal["clamp", "raise"]
PointPolicy = Literal["clamp", "raise"]

_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


# ---------------------------------------------------------------------- #
# 序列化
# ---------------------------------------------------------------------- #
def profile_to_dict(
    plan: CapturePlan,
    *,
    name: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """计划 → 档案字典（去掉 ``run``，补 ``kind`` / ``created_at``）。

    调用 :meth:`CapturePlan.validate`，保证写出的档案一定能被重新加载。
    """

    plan.validate()
    payload = plan.to_dict()
    payload.pop("run", None)

    raw_output = payload.get("output") or {}
    output = {
        key: raw_output[key]
        for key in PROFILE_OUTPUT_FIELDS
        if raw_output.get(key) is not None
    }
    payload["output"] = output

    payload["kind"] = PROFILE_KIND
    payload["name"] = (name or payload.get("name") or "").strip() or "capture-profile"
    payload["created_at"] = created_at or _now()
    return payload


def profile_from_dict(data: Mapping[str, Any]) -> CapturePlan:
    """档案字典 → :class:`CapturePlan`，并完成全部校验。"""

    if not isinstance(data, Mapping):
        raise ConfigError(f"配置档案必须是 JSON 对象，收到：{type(data).__name__}")
    kind = data.get("kind")
    if kind != PROFILE_KIND:
        raise ConfigError(
            f"不是配置档案（kind={kind!r}，期望 {PROFILE_KIND!r}）；"
            "若这是运行计划，请改用 load_plan"
        )

    body = {key: value for key, value in data.items() if key not in PROFILE_META_FIELDS}
    body.pop("run", None)
    # 空 run 会被 RunConfig.from_dict 全部取默认值（已由 tests 固化该契约）
    body["run"] = {}

    try:
        return plan_from_dict(body)
    except ConfigError as exc:
        raise ConfigError(f"配置档案校验失败：{exc}") from exc


def capture_plan_from_dict(data: Mapping[str, Any]) -> CapturePlan:
    """把「外部导入」的 JSON 归一到 :class:`CapturePlan`，分三种输入处理。

    ==================  ==========================================  ===============
    输入                 判别                                         处理
    ==================  ==========================================  ===============
    配置档案             ``kind == "profile"``                        ``profile_from_dict``
    运行计划             ``kind`` 为 ``None`` / ``"plan"``，且含       ``plan_from_dict``
                         ``regions`` 与 ``region_configs``
    未知                 ``kind`` 其它取值，或缺少必要字段             给出缺少了哪些字段
    ==================  ==========================================  ===============

    与 :func:`load_profile` 不同，本函数接受「带 run 的计划」，用于前端上传入口。
    """

    if not isinstance(data, Mapping):
        raise ConfigError(f"导入内容必须是 JSON 对象，收到：{type(data).__name__}")

    kind = data.get("kind")
    if kind == PROFILE_KIND:
        return profile_from_dict(data)
    if kind not in (None, "plan"):
        raise ConfigError(
            f"无法识别的文件：kind={kind!r}（应为 {PROFILE_KIND!r}、'plan' 或省略）"
        )

    missing = [name for name in ("regions", "region_configs") if name not in data]
    if missing:
        raise ConfigError(
            "无法识别的文件：既不是配置档案"
            f"（缺少 kind={PROFILE_KIND!r}），也不是运行计划"
            f"（缺少字段：{', '.join(missing)}）"
        )

    body = {key: value for key, value in data.items() if key != "kind"}
    try:
        return plan_from_dict(body)
    except ConfigError as exc:
        raise ConfigError(f"运行计划校验失败：{exc}") from exc


# ---------------------------------------------------------------------- #
# 文件读写
# ---------------------------------------------------------------------- #
def load_profile(path: str | Path) -> CapturePlan:
    """从 JSON 文件加载配置档案。"""

    profile_path = Path(path)
    if not profile_path.is_file():
        raise ConfigError(f"配置档案不存在：{profile_path}")
    try:
        raw = json.loads(profile_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"配置档案不是合法 JSON：{profile_path.name}"
            f"（第 {exc.lineno} 行第 {exc.colno} 列：{exc.msg}）"
        ) from exc
    except OSError as exc:
        raise ConfigError(f"无法读取配置档案：{profile_path}（{exc}）") from exc
    return profile_from_dict(raw)


def save_profile(
    plan: CapturePlan,
    path: str | Path,
    *,
    name: str | None = None,
) -> Path:
    """把计划保存为配置档案（原子写入，先校验后落盘）。

    参数是**已经构建并校验通过**的 :class:`CapturePlan`；调用方应先执行
    ``PlanBuilder.build()`` / ``try_build()``，把校验错误展示给用户，
    避免写出永远无法加载的档案。
    """

    payload = profile_to_dict(plan, name=name)
    profile_path = Path(path)
    _atomic_write_json(profile_path, payload)
    return profile_path


def list_profiles(directory: str | Path) -> list[Path]:
    """列出目录下的档案文件（修改时间倒序；目录不存在时返回空列表）。"""

    folder = Path(directory)
    if not folder.is_dir():
        return []
    found: list[Path] = []
    try:
        candidates = list(folder.glob(f"*{PROFILE_SUFFIX}"))
    except OSError:
        return []
    for candidate in candidates:
        try:
            if candidate.is_file():
                found.append(candidate)
        except OSError:
            continue
    return sorted(found, key=_mtime, reverse=True)


def profile_filename(name: str) -> str:
    """档案名 → 安全的文件名（保留中文，剔除 Windows 非法字符与保留名）。"""

    cleaned = _INVALID_FILENAME.sub("_", str(name or "").strip()).strip(" .")
    cleaned = cleaned[:60].strip(" .")
    if not cleaned:
        raise ConfigError("档案名不能为空（去掉非法字符后为空）")
    if cleaned.split(".")[0].upper() in _WINDOWS_RESERVED:
        cleaned = f"profile_{cleaned}"
    return f"{cleaned}{PROFILE_SUFFIX}"


# ---------------------------------------------------------------------- #
# 跨分辨率换算
# ---------------------------------------------------------------------- #
def rescale_plan(
    plan: CapturePlan,
    target: ScreenInfo,
    *,
    min_size_policy: MinSizePolicy = "clamp",
    point_policy: PointPolicy = "clamp",
) -> tuple[CapturePlan, list[str]]:
    """把计划按截图像素比例换算到 ``target`` 几何，返回 ``(新计划, 警告列表)``。

    - 只比较 ``screenshot_*``：区域坐标的基准是截图像素；
    - ``scroll_distance`` 不缩放（滚轮单位，不是像素）；
    - 抄录区域缩放后不足 20×20 时，``min_size_policy="clamp"``（默认）以中心扩展
      到最小尺寸并给出警告，``"raise"`` 则直接报错；
    - ``scroll_start`` / ``click_anchor`` 缩放后落在区域外时，``point_policy="clamp"``
      （默认）夹回区域内并警告，``"raise"`` 直接报错。
    """

    plan.validate()
    source = plan.screen
    warnings: list[str] = []

    same_shot = (
        source.screenshot_width == target.screenshot_width
        and source.screenshot_height == target.screenshot_height
    )

    if same_shot:
        regions = dict(plan.regions)
        configs = dict(plan.region_configs)
    else:
        scale_x = target.screenshot_width / source.screenshot_width
        scale_y = target.screenshot_height / source.screenshot_height
        regions = {}
        for name, region in plan.regions.items():
            config = plan.region_configs[name]
            minimum = (
                MIN_TRANSCRIBE_SIZE if isinstance(config, TranscribeConfig) else 1
            )
            coords = _scale_rect(region.as_list(), scale_x, scale_y)
            coords = _clamp_rect(
                coords, target.screenshot_width, target.screenshot_height
            )
            coords, extended = _fit_min_size(
                coords, minimum, target.screenshot_width, target.screenshot_height
            )
            if extended:
                message = (
                    f"区域 {name} 缩放后小于 {minimum}×{minimum}"
                    f"（{coords[2] - coords[0]}×{coords[3] - coords[1]} 已扩展），"
                    f"请确认框选范围是否仍然合适"
                )
                if min_size_policy == "raise":
                    raise ConfigError(
                        f"区域 {name} 缩放后小于最小尺寸 {minimum}×{minimum}："
                        f"请重新框选，或换回原分辨率 "
                        f"{source.screenshot_width}×{source.screenshot_height}"
                    )
                warnings.append(message)
            regions[name] = Region.from_list(name, coords)

        configs = {}
        for name, config in plan.region_configs.items():
            if isinstance(config, SmsConfig):
                start = _scale_and_fit_point(
                    config.scroll_start,
                    scale_x,
                    scale_y,
                    regions[name],
                    f"区域 {name} 的 scroll_start",
                    point_policy,
                    warnings,
                )
                anchor = _scale_and_fit_point(
                    config.click_anchor,
                    scale_x,
                    scale_y,
                    regions[name],
                    f"区域 {name} 的 click_anchor",
                    point_policy,
                    warnings,
                )
                configs[name] = replace(
                    config, scroll_start=start, click_anchor=anchor
                )
            elif isinstance(config, TranscribeConfig):
                start = _scale_and_fit_point(
                    config.scroll_start,
                    scale_x,
                    scale_y,
                    regions[name],
                    f"区域 {name} 的 scroll_start",
                    point_policy,
                    warnings,
                )
                configs[name] = replace(config, scroll_start=start)
            else:  # pragma: no cover - 目前只有两种配置类型
                raise ConfigError(f"区域 {name} 的配置类型无法缩放：{type(config).__name__}")

    scaled = CapturePlan(
        regions=regions,
        region_configs=configs,
        screen=target,
        run=plan.run,
        output=plan.output,
        name=plan.name,
        schema_version=plan.schema_version,
    )
    try:
        scaled.validate()
    except ConfigError as exc:
        raise ConfigError(f"缩放后计划校验失败：{exc}") from exc
    return scaled, warnings


def _scale_rect(coords: list[int], scale_x: float, scale_y: float) -> list[int]:
    x1, y1, x2, y2 = coords
    return [
        int(round(x1 * scale_x)),
        int(round(y1 * scale_y)),
        int(round(x2 * scale_x)),
        int(round(y2 * scale_y)),
    ]


def _clamp_rect(coords: list[int], width: int, height: int) -> list[int]:
    """把矩形夹进截图边界（``validate()`` 已保证不越界，这里是浮点舍入的兜底）。"""

    x1, y1, x2, y2 = coords
    x1 = max(0, min(x1, width - 1))
    y1 = max(0, min(y1, height - 1))
    x2 = max(x1 + 1, min(x2, width))
    y2 = max(y1 + 1, min(y2, height))
    return [x1, y1, x2, y2]


def _fit_min_size(
    coords: list[int], minimum: int, width: int, height: int
) -> tuple[list[int], bool]:
    """把过小的区域以中心为基准扩展到 ``minimum``×``minimum`` 并夹回边界。"""

    x1, y1, x2, y2 = coords
    changed = False
    min_w = min(max(1, minimum), width)
    min_h = min(max(1, minimum), height)

    if x2 - x1 < min_w:
        center = (x1 + x2) / 2
        x1 = int(round(center - min_w / 2))
        x2 = x1 + min_w
        changed = True
    if y2 - y1 < min_h:
        center = (y1 + y2) / 2
        y1 = int(round(center - min_h / 2))
        y2 = y1 + min_h
        changed = True

    # 平移回边界内（只平移，不改尺寸）
    if x1 < 0:
        x2 -= x1
        x1 = 0
    if y1 < 0:
        y2 -= y1
        y1 = 0
    if x2 > width:
        x1 -= x2 - width
        x2 = width
    if y2 > height:
        y1 -= y2 - height
        y2 = height

    x1, y1 = max(0, x1), max(0, y1)
    if x2 <= x1 or y2 <= y1:
        raise ConfigError(
            f"区域缩放后尺寸非法（{x2 - x1}×{y2 - y1}），请重新框选"
        )
    return [x1, y1, x2, y2], changed


def _scale_and_fit_point(
    point: list[int] | None,
    scale_x: float,
    scale_y: float,
    region: Region,
    label: str,
    policy: PointPolicy,
    warnings: list[str],
) -> list[int] | None:
    if point is None:
        return None
    scaled = [
        int(round(point[0] * scale_x)),
        int(round(point[1] * scale_y)),
    ]
    if region.contains_point(scaled[0], scaled[1]):
        return scaled

    fitted = _clamp_point_into_region(scaled, region)
    message = (
        f"{label} 缩放后落在区域外（{scaled[0]}, {scaled[1]}），已夹回 "
        f"（{fitted[0]}, {fitted[1]}）；请确认该坐标仍指向要滚动的列表"
    )
    if policy == "raise":
        raise ConfigError(f"{label} 缩放后落在区域 {region.name} 之外：请重新拾取该坐标")
    warnings.append(message)
    return fitted


def _clamp_point_into_region(point: list[int], region: Region) -> list[int]:
    """把点夹到区域内（留 1px 内边距；区域过小时取中心）。"""

    x1, x2 = region.xmin, region.xmax
    y1, y2 = region.ymin, region.ymax
    if x2 - x1 <= 2 or y2 - y1 <= 2:
        return [(x1 + x2) // 2, (y1 + y2) // 2]
    return [
        max(x1 + 1, min(point[0], x2 - 1)),
        max(y1 + 1, min(point[1], y2 - 1)),
    ]


# ---------------------------------------------------------------------- #
# 内部工具
# ---------------------------------------------------------------------- #
def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """写临时文件后 ``os.replace``：中途失败不留半截 JSON。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    handle_fd, temp_name = tempfile.mkstemp(
        prefix=f"{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
