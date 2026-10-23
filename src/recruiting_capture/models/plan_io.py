"""计划文件的读写与序列化（JSON）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from ..errors import ConfigError
from .config import (
    CapturePlan,
    OutputConfig,
    RunConfig,
    ScreenInfo,
    config_from_dict,
)
from .region import Region

_TOP_LEVEL_FIELDS = (
    "schema_version",
    "name",
    "screen",
    "regions",
    "region_configs",
    "output",
    "run",
)


def plan_from_dict(data: Mapping[str, Any]) -> CapturePlan:
    """字典 → CapturePlan，并完成全部校验。"""

    if not isinstance(data, Mapping):
        raise ConfigError(f"计划必须是 JSON 对象，收到：{type(data).__name__}")
    unknown = sorted(set(data) - set(_TOP_LEVEL_FIELDS))
    if unknown:
        raise ConfigError(f"计划包含未知字段：{', '.join(unknown)}")

    raw_regions = data.get("regions")
    if not isinstance(raw_regions, Mapping) or not raw_regions:
        raise ConfigError("计划必须包含非空的 regions 区域字典")
    regions: dict[str, Region] = {}
    for name, coords in raw_regions.items():
        regions[str(name)] = Region.from_list(str(name), coords)

    raw_configs = data.get("region_configs")
    if not isinstance(raw_configs, Mapping) or not raw_configs:
        raise ConfigError("计划必须包含非空的 region_configs 区域配置字典")
    region_configs = {
        str(name): config_from_dict(value) for name, value in raw_configs.items()
    }

    screen = ScreenInfo.from_dict(data.get("screen") or {})
    output = OutputConfig.from_dict(data.get("output"))
    run = RunConfig.from_dict(data.get("run"))
    name = data.get("name", "capture-plan")
    if not isinstance(name, str) or not name.strip():
        raise ConfigError("计划 name 必须是非空字符串")

    plan = CapturePlan(
        regions=regions,
        region_configs=region_configs,
        screen=screen,
        run=run,
        output=output,
        name=name.strip(),
        schema_version=str(data.get("schema_version", "1.0")),
    )
    plan.validate()
    return plan


def load_plan(path: str | Path) -> CapturePlan:
    """从 JSON 文件加载并校验计划。"""

    plan_path = Path(path)
    if not plan_path.is_file():
        raise ConfigError(f"计划文件不存在：{plan_path}")
    try:
        raw = json.loads(plan_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"计划文件不是合法 JSON：{plan_path}（第 {exc.lineno} 行第 {exc.colno} 列：{exc.msg}）"
        ) from exc
    except OSError as exc:
        raise ConfigError(f"无法读取计划文件：{plan_path}（{exc}）") from exc
    try:
        return plan_from_dict(raw)
    except ConfigError as exc:
        raise ConfigError(f"计划校验失败（{plan_path.name}）：{exc}") from exc


def plan_to_dict(plan: CapturePlan) -> dict[str, Any]:
    plan.validate()
    return plan.to_dict()


def save_plan(plan: CapturePlan, path: str | Path) -> Path:
    """保存计划为 JSON（UTF-8，不转义中文）。"""

    plan_path = Path(path)
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    payload = plan_to_dict(plan)
    plan_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return plan_path
