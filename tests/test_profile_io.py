"""配置档案（Profile）的读写、校验与跨分辨率换算。

本文件的第一条用例（``test_plan_from_dict_accepts_empty_run``）是整条方案的
**前提契约**：档案与计划同构、去掉 ``run`` 补空字典后仍能被 ``plan_from_dict``
接受，才允许加载路径复用 ``plan_from_dict`` 的全部校验。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from recruiting_capture.errors import ConfigError
from recruiting_capture.models.config import (
    CapturePlan,
    OutputConfig,
    RunConfig,
    ScreenInfo,
    SmsConfig,
    TranscribeConfig,
)
from recruiting_capture.models.plan_io import plan_from_dict
from recruiting_capture.models.profile_io import (
    MIN_TRANSCRIBE_SIZE,
    capture_plan_from_dict,
    list_profiles,
    load_profile,
    profile_filename,
    profile_from_dict,
    profile_to_dict,
    rescale_plan,
    save_profile,
)
from recruiting_capture.models.region import Region

# ---------------------------------------------------------------------- #
# 夹具
# ---------------------------------------------------------------------- #
def _make_plan(
    *,
    b_coords: tuple[int, int, int, int] = (900, 900, 925, 925),
    scroll_start: tuple[int, int] = (300, 500),
    screen: tuple[int, int] = (1920, 1080),
) -> CapturePlan:
    """构造一份最小但完整合法的计划（A 短信 / B 列表抄录 / C 标题抄录）。"""

    regions = {
        "A": Region.from_list("A", [100, 100, 500, 900]),
        "B": Region.from_list("B", list(b_coords)),
        "C": Region.from_list("C", [600, 100, 1000, 200]),
    }
    region_configs = {
        "A": SmsConfig(
            scroll_distance=-180,
            scroll_start=list(scroll_start),
            scroll_stop_markers=["企业服务"],
            invoke_regions=["C", "B"],
            data_structure="dict",
            key_region="C",
        ),
        "B": TranscribeConfig(
            scrollable=False, expected_content="职位描述", expected_format="list"
        ),
        "C": TranscribeConfig(
            scrollable=False, expected_content="岗位名称", expected_format="str"
        ),
    }
    width, height = screen
    plan = CapturePlan(
        regions=regions,
        region_configs=region_configs,
        screen=ScreenInfo(width, height, width, height),
        output=OutputConfig(title_region="C", desc_region="B"),
    )
    plan.validate()
    return plan


# ---------------------------------------------------------------------- #
# 前提契约：空 run 能被 plan_from_dict 接受
# ---------------------------------------------------------------------- #
def test_plan_from_dict_accepts_empty_run(example_plan: CapturePlan) -> None:
    """档案格式成立的前提：``run={}`` 不触发必填校验，全部取默认值。"""

    data = example_plan.to_dict()
    data["run"] = {}
    plan = plan_from_dict(data)

    defaults = RunConfig()
    assert plan.run.max_jobs == defaults.max_jobs
    assert plan.run.engine == defaults.engine
    assert plan.run.llm == defaults.llm


# ---------------------------------------------------------------------- #
# 往返
# ---------------------------------------------------------------------- #
def test_profile_roundtrip(example_plan: CapturePlan, tmp_path: Path) -> None:
    path = save_profile(example_plan, tmp_path / "boss.json", name="boss_1080p")
    raw = json.loads(path.read_text(encoding="utf-8"))

    assert raw["kind"] == "profile"
    assert raw["name"] == "boss_1080p"
    assert "run" not in raw
    assert raw["schema_version"] == "1.0"
    assert set(raw["output"]) == {"title_region", "desc_region"}

    loaded = load_profile(path)
    assert loaded.screen == example_plan.screen
    assert {name: r.as_list() for name, r in loaded.regions.items()} == {
        name: r.as_list() for name, r in example_plan.regions.items()
    }
    assert loaded.region_configs["A"].to_dict() == (
        example_plan.region_configs["A"].to_dict()
    )
    assert isinstance(loaded.region_configs["A"], SmsConfig)
    assert isinstance(loaded.region_configs["B"], TranscribeConfig)
    assert loaded.output.title_region == example_plan.output.title_region
    assert loaded.output.desc_region == example_plan.output.desc_region


def test_save_leaves_no_temp_file(example_plan: CapturePlan, tmp_path: Path) -> None:
    path = save_profile(example_plan, tmp_path / "boss.json")
    assert path.is_file()
    assert list(tmp_path.glob("*.tmp")) == []
    json.loads(path.read_text(encoding="utf-8"))  # 原子写入后内容完整


def test_profile_keeps_chinese_name(tmp_path: Path) -> None:
    plan = _make_plan()
    path = save_profile(plan, tmp_path / "档案.json", name="BOSS 1080P·测试")
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["name"] == "BOSS 1080P·测试"
    assert load_profile(path).screen.screenshot_width == 1920


# ---------------------------------------------------------------------- #
# 校验必须复用 plan_from_dict
# ---------------------------------------------------------------------- #
def test_profile_rejects_wrong_kind() -> None:
    data = profile_to_dict(_make_plan())
    data["kind"] = "plan"
    with pytest.raises(ConfigError, match="不是配置档案"):
        profile_from_dict(data)


def test_profile_rejects_non_mapping() -> None:
    with pytest.raises(ConfigError, match="JSON 对象"):
        profile_from_dict([])  # type: ignore[arg-type]


def test_profile_rejects_unknown_field() -> None:
    data = profile_to_dict(_make_plan())
    data["foo"] = 1
    with pytest.raises(ConfigError, match="未知字段"):
        profile_from_dict(data)


def test_profile_reports_missing_screen_as_config_error() -> None:
    data = profile_to_dict(_make_plan())
    data.pop("screen")
    with pytest.raises(ConfigError, match="配置档案校验失败"):
        profile_from_dict(data)


def test_profile_reuses_cross_region_validation() -> None:
    """手工改坏的引用必须在**加载时**报错，而不是运行期崩溃。"""

    data = profile_to_dict(_make_plan())
    # 追加 D 而不是替换：替换会先触发 key_region 必须在 invoke_regions 中的校验，
    # 掩盖本用例真正要验证的跨区域引用检查。
    data["region_configs"]["A"]["invoke_regions"] = ["C", "B", "D"]
    with pytest.raises(ConfigError, match="不存在的区域 D"):
        profile_from_dict(data)


def test_profile_rejects_region_out_of_bounds() -> None:
    data = profile_to_dict(_make_plan())
    data["regions"]["A"] = [0, 0, 5000, 6000]
    with pytest.raises(ConfigError, match="超出屏幕范围"):
        profile_from_dict(data)


def test_load_profile_reports_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="不是合法 JSON"):
        load_profile(path)


def test_load_profile_reports_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="不存在"):
        load_profile(tmp_path / "nope.json")


def test_save_profile_refuses_invalid_plan(tmp_path: Path) -> None:
    """保存前必须校验：坏计划不得落盘，否则会写出永远加载不了的档案。

    这里构造的是"绕过 ``SmsConfig.from_dict`` 直接造出来的非法配置"
    （``scroll_distance=0`` 且没有停止条件），用来确认校验不依赖 ``from_dict``
    这条路径：``CapturePlan.validate`` 必须自己调用各区域的 ``validate``。
    """

    plan = _make_plan()
    plan.region_configs["A"] = SmsConfig(
        scroll_distance=0,  # 非法：可滚动区域必须非 0
        scroll_start=[300, 500],
        scroll_stop_markers=[],  # 非法：必须至少一个停止条件
        invoke_regions=["C", "B"],
        data_structure="dict",
        key_region="C",
    )
    target = tmp_path / "bad.json"
    with pytest.raises(ConfigError, match="scroll_distance"):
        save_profile(plan, target)
    assert not target.exists()
    assert list(tmp_path.glob("*.tmp")) == []  # 校验失败时连临时文件都不应留下


def test_capture_plan_validate_checks_each_region_config() -> None:
    """回归：``CapturePlan.validate`` 必须调用各区域配置的 ``validate``。

    历史实现只做跨区域检查，绕过 ``from_dict`` 直接构造的计划可以带着
    ``scroll_distance=0``、空停止条件一路通过。
    """

    plan = _make_plan()
    plan.region_configs["A"] = SmsConfig(
        scroll_distance=0,
        scroll_start=[300, 500],
        scroll_stop_markers=[],
        invoke_regions=["C", "B"],
        data_structure="dict",
        key_region="C",
    )
    with pytest.raises(ConfigError, match="scroll_distance"):
        plan.validate()


# ---------------------------------------------------------------------- #
# 跨分辨率换算
# ---------------------------------------------------------------------- #
def test_rescale_uses_screenshot_size_not_screen_size() -> None:
    """DPI 场景：屏幕尺寸变了但截图像素没变时，坐标不应被缩放。"""

    plan = _make_plan()
    scaled, warnings = rescale_plan(plan, ScreenInfo(1536, 864, 1920, 1080))

    assert warnings == []
    assert scaled.screen.width == 1536
    assert scaled.regions["A"].as_list() == [100, 100, 500, 900]


def test_rescale_halves_coordinates() -> None:
    plan = _make_plan()
    scaled, _ = rescale_plan(plan, ScreenInfo(960, 540, 960, 540))

    assert scaled.regions["A"].as_list() == [50, 50, 250, 450]
    assert scaled.regions["C"].as_list() == [300, 50, 500, 100]


def test_rescale_never_scales_scroll_distance() -> None:
    """``scroll_distance`` 是滚轮单位，不是像素。"""

    plan = _make_plan()
    scaled, _ = rescale_plan(plan, ScreenInfo(1280, 720, 1280, 720))

    config = scaled.region_configs["A"]
    assert isinstance(config, SmsConfig)
    assert config.scroll_distance == -180


def test_rescale_scales_scroll_start() -> None:
    plan = _make_plan()
    scaled, warnings = rescale_plan(plan, ScreenInfo(1280, 720, 1280, 720))

    config = scaled.region_configs["A"]
    assert isinstance(config, SmsConfig)
    assert config.scroll_start == [200, 333]
    # 仍在缩放后的区域 A（[67,67,333,600]）内，不应产生 scroll_start 警告；
    # B 区域另会触发最小尺寸警告，这里只看滚动起点。
    assert not any("scroll_start" in message for message in warnings)


def test_rescale_min_size_violation_raises() -> None:
    plan = _make_plan()  # B 为 25x25，缩到 2/3 后为 17x17
    with pytest.raises(ConfigError, match="最小尺寸"):
        rescale_plan(
            plan, ScreenInfo(1280, 720, 1280, 720), min_size_policy="raise"
        )


def test_rescale_min_size_clamps_by_default() -> None:
    plan = _make_plan()
    scaled, warnings = rescale_plan(plan, ScreenInfo(1280, 720, 1280, 720))

    region_b = scaled.regions["B"]
    assert region_b.width >= MIN_TRANSCRIBE_SIZE
    assert region_b.height >= MIN_TRANSCRIBE_SIZE
    assert any("区域 B" in message for message in warnings)
    scaled.validate()  # 扩展后仍是合法计划


def test_rescale_clamps_scroll_start_into_region() -> None:
    """缩放后落到区域外的滚动起点必须夹回，并给出可读警告。"""

    plan = _make_plan(scroll_start=(1500, 1000))  # 本来就在区域 A 之外
    scaled, warnings = rescale_plan(plan, ScreenInfo(1280, 720, 1280, 720))

    region_a = scaled.regions["A"]
    config = scaled.region_configs["A"]
    assert isinstance(config, SmsConfig)
    assert config.scroll_start is not None
    assert region_a.contains_point(*config.scroll_start)
    assert any("scroll_start" in message for message in warnings)


def test_rescale_point_policy_raise() -> None:
    plan = _make_plan(scroll_start=(1500, 1000))
    with pytest.raises(ConfigError, match="scroll_start"):
        rescale_plan(
            plan,
            ScreenInfo(1280, 720, 1280, 720),
            point_policy="raise",
        )


def test_rescale_identity_is_stable() -> None:
    plan = _make_plan()
    scaled, warnings = rescale_plan(plan, ScreenInfo(1920, 1080, 1920, 1080))

    assert warnings == []
    assert scaled.regions["A"].as_list() == plan.regions["A"].as_list()
    assert scaled.region_configs["A"].to_dict() == (
        plan.region_configs["A"].to_dict()
    )


# ---------------------------------------------------------------------- #
# 上传入口：三种输入的判别
# ---------------------------------------------------------------------- #
def test_import_accepts_profile(example_plan: CapturePlan) -> None:
    data = profile_to_dict(example_plan, name="boss_1080p")
    plan = capture_plan_from_dict(data)
    assert plan.screen == example_plan.screen
    assert plan.run.max_jobs == RunConfig().max_jobs


def test_import_accepts_full_plan(example_plan: CapturePlan) -> None:
    plan = capture_plan_from_dict(example_plan.to_dict())
    assert plan.name == example_plan.name
    assert plan.run.max_jobs == example_plan.run.max_jobs


def test_import_accepts_plan_with_kind(example_plan: CapturePlan) -> None:
    data = example_plan.to_dict()
    data["kind"] = "plan"
    plan = capture_plan_from_dict(data)
    assert plan.regions["A"].as_list() == example_plan.regions["A"].as_list()


def test_import_unknown_input_reports_missing_fields() -> None:
    with pytest.raises(ConfigError, match="缺少字段：regions, region_configs"):
        capture_plan_from_dict({"name": "whatever"})


def test_import_unknown_kind_is_reported() -> None:
    with pytest.raises(ConfigError, match="kind='foo'"):
        capture_plan_from_dict({"kind": "foo"})


def test_import_broken_plan_is_reported() -> None:
    data = profile_to_dict(_make_plan())
    data["region_configs"]["A"]["invoke_regions"] = ["C", "B", "D"]
    data.pop("kind")
    with pytest.raises(ConfigError, match="运行计划校验失败"):
        capture_plan_from_dict(data)


def test_import_rejects_non_mapping() -> None:
    with pytest.raises(ConfigError, match="JSON 对象"):
        capture_plan_from_dict("nope")  # type: ignore[arg-type]


# ---------------------------------------------------------------------- #
# 文件名与列表
# ---------------------------------------------------------------------- #
def test_profile_filename_sanitizes_windows_illegal_chars() -> None:
    name = profile_filename('a/b:c*?"<>|')
    assert name.endswith(".json")
    assert not set(name) & set('<>:"/\\|?*')


def test_profile_filename_escapes_reserved_names() -> None:
    assert profile_filename("CON") == "profile_CON.json"
    assert profile_filename("lpt1") == "profile_lpt1.json"


def test_profile_filename_rejects_empty() -> None:
    with pytest.raises(ConfigError, match="不能为空"):
        profile_filename("   ")


def test_list_profiles_sorted_by_mtime(tmp_path: Path) -> None:
    older = tmp_path / "older.json"
    newer = tmp_path / "newer.json"
    older.write_text("{}", encoding="utf-8")
    newer.write_text("{}", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("ignore me", encoding="utf-8")

    os.utime(older, (1_700_000_000, 1_700_000_000))
    os.utime(newer, (1_700_000_100, 1_700_000_100))

    found = list_profiles(tmp_path)
    assert [path.name for path in found] == ["newer.json", "older.json"]


def test_list_profiles_missing_dir(tmp_path: Path) -> None:
    assert list_profiles(tmp_path / "nope") == []
