"""计划构建器（与 Streamlit 无关的 UI 逻辑）。"""

from __future__ import annotations

import pytest

from recruiting_capture.errors import ConfigError
from recruiting_capture.plan_builder import PlanBuilder


def test_builder_adds_regions_in_order() -> None:
    builder = PlanBuilder(1920, 1080)
    assert builder.add_region([200, 300, 500, 700]) == "A"
    assert builder.add_region([600, 300, 1400, 700]) == "B"
    assert builder.next_name() == "C"


def test_builder_reuses_removed_name() -> None:
    builder = PlanBuilder(1920, 1080)
    builder.add_region([200, 300, 500, 700])
    builder.add_region([600, 300, 900, 700])
    builder.remove_region("A")
    assert builder.add_region([300, 300, 500, 700]) == "A"


def test_builder_rejects_too_small_region() -> None:
    builder = PlanBuilder(1920, 1080)
    with pytest.raises(ConfigError):
        builder.add_region([10, 10, 15, 15])


def test_mode_switch_resets_defaults() -> None:
    builder = PlanBuilder(1920, 1080)
    name = builder.add_region([200, 300, 500, 700])
    assert builder.config(name)["mode"] == "transcribe"
    builder.set_mode(name, "sms")
    assert builder.config(name)["mode"] == "sms"
    assert builder.config(name)["invoke_regions"] == []
    builder.set_mode(name, "transcribe")
    assert builder.config(name)["mode"] == "transcribe"


def test_update_config_rejects_unknown_key() -> None:
    builder = PlanBuilder(1920, 1080)
    name = builder.add_region([200, 300, 500, 700])
    with pytest.raises(ConfigError):
        builder.update_config(name, {"nope": 1})


def test_builder_roundtrip_from_plan(demo_plan) -> None:
    builder = PlanBuilder.from_plan(demo_plan)
    assert builder.regions == {name: r.as_list() for name, r in demo_plan.regions.items()}
    plan = builder.build(engine="mock", llm="mock")
    assert plan.regions["B"].as_list() == demo_plan.regions["B"].as_list()
    assert plan.output.title_region == demo_plan.output.title_region


def test_builder_try_build_reports_errors() -> None:
    builder = PlanBuilder(1920, 1080)
    name = builder.add_region([200, 300, 500, 700])
    builder.add_region([600, 300, 1400, 700])
    builder.set_mode(name, "sms")
    plan, errors = builder.try_build()
    assert plan is None
    assert errors


def test_builder_suggests_output_regions(demo_plan) -> None:
    builder = PlanBuilder.from_plan(demo_plan)
    builder.set_output_regions(None, None)
    title, desc = builder.suggest_output_regions()
    assert title == "C"
    assert desc == "B"


def test_builder_requires_regions() -> None:
    builder = PlanBuilder(1920, 1080)
    with pytest.raises(ConfigError):
        builder.build()
