"""拟人化配置：默认值、逐项校验、计划往返、驱动注入与生命周期。"""

from __future__ import annotations

import pytest

from recruiting_capture.capture.pyautogui_driver import HumanProfile
from recruiting_capture.errors import ConfigError
from recruiting_capture.models.config import RunConfig
from recruiting_capture.models.humanize import HumanizeConfig
from recruiting_capture.models.plan_io import plan_from_dict, plan_to_dict


def test_defaults_and_profile_conversion() -> None:
    cfg = HumanizeConfig()
    assert cfg.enabled is True
    assert cfg.idle_wander is True
    assert cfg.overshoot_px == (10.0, 110.0)
    assert cfg.scroll_step == (110, 130)
    assert cfg.block_every == (12, 30)

    profile = cfg.to_profile()
    assert isinstance(profile, HumanProfile)
    assert profile.drift_px == cfg.drift_px
    assert profile.block_duration == cfg.block_duration


def test_run_config_defaults_and_roundtrip() -> None:
    run = RunConfig.from_dict({})
    assert run.humanize.enabled is True
    data = run.to_dict()
    assert data["humanize"]["block_every"] == [12, 30]
    assert data["humanize"]["move_duration"] == [0.18, 0.55]
    assert RunConfig.from_dict(data).humanize == run.humanize


def test_partial_override_keeps_other_defaults() -> None:
    cfg = HumanizeConfig.from_dict(
        {
            "enabled": False,
            "idle_wander": False,
            "drift_px": 0.0,
            "block_every": None,
            "move_duration": [0.2, 0.3],
        }
    )
    assert cfg.enabled is False
    assert cfg.idle_wander is False
    assert cfg.block_every is None
    assert cfg.move_duration == (0.2, 0.3)
    assert cfg.pre_click_pause == (0.08, 0.28)
    assert cfg.to_dict()["block_every"] is None


def test_unknown_key_rejected() -> None:
    with pytest.raises(ConfigError, match="未知配置项"):
        HumanizeConfig.from_dict({"drift_px": 1.0, "unknown_key": 1})


def test_integral_floats_accepted_for_integer_ranges() -> None:
    cfg = HumanizeConfig.from_dict(
        {"scroll_step": [110.0, 130.0], "block_every": [2.0, 4.0]}
    )
    assert cfg.scroll_step == (110, 130)
    assert cfg.block_every == (2, 4)


@pytest.mark.parametrize(
    "payload",
    [
        {"move_duration": [0.3, 0.1]},  # 下界大于上界
        {"move_duration": [0.1]},  # 不是二元组
        {"move_duration": "0.2-0.3"},  # 不是数组
        {"drift_px": -1},  # 负数
        {"overshoot_chance": 1.5},  # 概率越界
        {"settle_times": [1.5, 2]},  # 次数必须是整数
        {"block_every": [0, 3]},  # 至少每 1 次
        {"scroll_overshoot": "yes"},  # 必须是布尔值
        {"enabled": 1},  # 必须是布尔值
        {"humanize": {"bogus": 1}},  # run.humanize 内的未知字段
    ],
)
def test_invalid_values_rejected(payload) -> None:
    if "humanize" in payload:
        with pytest.raises(ConfigError, match="未知配置项"):
            RunConfig.from_dict(payload)
    else:
        with pytest.raises(ConfigError):
            HumanizeConfig.from_dict(payload)


def test_plan_roundtrip_preserves_humanize(demo_plan) -> None:
    data = plan_to_dict(demo_plan)
    data["run"]["humanize"] = {"enabled": False, "drift_px": 0.75}
    plan = plan_from_dict(data)
    assert plan.run.humanize.enabled is False
    assert plan.run.humanize.drift_px == 0.75
    assert plan_to_dict(plan)["run"]["humanize"]["drift_px"] == 0.75


def test_build_driver_injects_humanize_config(monkeypatch, demo_plan, project_root) -> None:
    import recruiting_capture.capture.pyautogui_driver as driver_module
    from recruiting_capture.engines import build_driver

    captured: dict = {}

    class FakeDriver:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)
            self.humanized = kwargs.get("humanize") is not False

    monkeypatch.setattr(driver_module, "PyAutoGuiDriver", FakeDriver)
    plan = plan_from_dict(plan_to_dict(demo_plan))
    plan.run.engine = "real"
    plan.run.humanize = HumanizeConfig.from_dict(
        {"idle_wander": False, "drift_px": 2.0, "block_every": [3, 5]}
    )

    driver = build_driver(plan, project_root)
    assert isinstance(driver, FakeDriver)
    assert captured["humanize"] is None, "enabled=true 时交给环境变量兜底"
    assert captured["idle_wander"] is False
    assert captured["human"].drift_px == 2.0
    assert captured["human"].block_every == (3, 5)

    plan.run.humanize = HumanizeConfig.from_dict({"enabled": False})
    build_driver(plan, project_root)
    assert captured["humanize"] is False, "enabled=false 必须显式关闭"


def test_close_driver_tolerates_missing_and_failing_close() -> None:
    from recruiting_capture.engines import close_driver

    close_driver(None)

    class _Driver:
        def close(self) -> None:
            raise RuntimeError("boom")

    close_driver(_Driver())  # 收尾失败不应抛出


def test_run_plan_closes_driver(tmp_path, demo_plan, project_root, no_sleep, monkeypatch) -> None:
    from recruiting_capture.capture.mock_driver import MockScreenDriver
    from recruiting_capture.orchestrator import run_plan

    closed: list[bool] = []
    monkeypatch.setattr(MockScreenDriver, "close", lambda self: closed.append(True))

    plan = plan_from_dict(plan_to_dict(demo_plan))
    plan.output.dir = str(tmp_path / "output")
    run_plan(plan, tmp_path / "run", project_root, sleep=no_sleep)

    assert closed == [True], "run_plan 的 finally 必须关闭驱动"
