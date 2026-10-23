"""计划/区域配置的校验与往返。"""

from __future__ import annotations

import copy

import pytest

from recruiting_capture.errors import ConfigError
from recruiting_capture.models.plan_io import (
    load_plan,
    plan_from_dict,
    plan_to_dict,
    save_plan,
)


def test_demo_plan_loads(demo_plan) -> None:
    assert set(demo_plan.regions) == {"A", "B", "C"}
    assert demo_plan.region_configs["A"].mode == "sms"
    assert demo_plan.region_configs["B"].mode == "transcribe"
    assert demo_plan.output.title_region == "C"
    assert demo_plan.output.desc_region == "B"
    assert demo_plan.run.max_jobs == 10


def test_plan_roundtrip(demo_plan, tmp_path) -> None:
    path = save_plan(demo_plan, tmp_path / "plan.json")
    loaded = load_plan(path)
    assert plan_to_dict(loaded) == plan_to_dict(demo_plan)
    assert loaded.regions["A"].as_list() == [212, 300, 620, 930]


def test_unknown_top_level_field_rejected(demo_plan) -> None:
    data = copy.deepcopy(plan_to_dict(demo_plan))
    data["unexpected"] = True
    with pytest.raises(ConfigError):
        plan_from_dict(data)


def test_unknown_region_config_field_rejected(demo_plan) -> None:
    data = copy.deepcopy(plan_to_dict(demo_plan))
    data["region_configs"]["B"]["mystery"] = 1
    with pytest.raises(ConfigError):
        plan_from_dict(data)


def test_region_coordinate_out_of_screen_rejected(demo_plan) -> None:
    data = copy.deepcopy(plan_to_dict(demo_plan))
    data["regions"]["A"] = [212, 300, 3000, 930]
    with pytest.raises(ConfigError):
        plan_from_dict(data)


def test_dict_mode_key_must_be_string(demo_plan) -> None:
    data = copy.deepcopy(plan_to_dict(demo_plan))
    data["region_configs"]["C"]["expected_format"] = "list"
    with pytest.raises(ConfigError):
        plan_from_dict(data)


def test_scrollable_requires_distance(demo_plan) -> None:
    data = copy.deepcopy(plan_to_dict(demo_plan))
    data["region_configs"]["B"]["scroll_distance"] = None
    with pytest.raises(ConfigError):
        plan_from_dict(data)


def test_sms_requires_invoke_regions(demo_plan) -> None:
    data = copy.deepcopy(plan_to_dict(demo_plan))
    data["region_configs"]["A"]["invoke_regions"] = []
    with pytest.raises(ConfigError):
        plan_from_dict(data)


def test_sms_invoke_must_be_transcribe(demo_plan) -> None:
    data = copy.deepcopy(plan_to_dict(demo_plan))
    data["region_configs"]["A"]["invoke_regions"] = ["A"]
    with pytest.raises(ConfigError):
        plan_from_dict(data)


def test_output_desc_must_not_be_string_format(demo_plan) -> None:
    data = copy.deepcopy(plan_to_dict(demo_plan))
    data["output"]["desc_region"] = "C"
    with pytest.raises(ConfigError):
        plan_from_dict(data)


def test_output_region_must_exist(demo_plan) -> None:
    data = copy.deepcopy(plan_to_dict(demo_plan))
    data["output"]["title_region"] = "Z9"
    with pytest.raises(ConfigError):
        plan_from_dict(data)


def test_invalid_engine_rejected(demo_plan) -> None:
    data = copy.deepcopy(plan_to_dict(demo_plan))
    data["run"]["engine"] = "selenium"
    with pytest.raises(ConfigError):
        plan_from_dict(data)


def test_missing_plan_file(tmp_path) -> None:
    with pytest.raises(ConfigError):
        load_plan(tmp_path / "nope.json")


def test_malformed_json(tmp_path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_plan(path)


def test_example_plan_loads(example_plan) -> None:
    assert example_plan.run.engine == "real"
    assert example_plan.run.llm == "deepseek"
    assert example_plan.run.minimize_frontend is True
    assert example_plan.run.max_jobs == 20
