"""区域配置的“唯一真值源”约束（与 Streamlit 无关）。

`docs/STREAMLIT_STATE_FIX_PLAN.md` 的结论是：配置页出现“旧值覆盖新配置”，
根源不在 `builder`，而在控件把值缓存在 session_state 里、不再从 builder 读取。
因此本文件守住 builder 侧必须始终成立的四条不变量：

1. 切换工作模式 = 整套配置换成该模式的默认值（旧值不能被残留）；
2. 抄录区域变成短信区域时，其它短信区域的 `invoke_regions` / `key_region` 不得再指向它；
3. 删除区域时同上，且输出映射随之清空；
4. 加载示例配置后，`configs` 就是文件里的值——UI 只要如实展示即可。

这些测试不依赖 Streamlit，也不会因组件版本升级而失效。
"""

from __future__ import annotations

import pytest

from recruiting_capture.models.config import (
    MODE_SMS,
    MODE_TRANSCRIBE,
    default_sms_config,
    default_transcribe_config,
)
from recruiting_capture.plan_builder import PlanBuilder

# configs/boss_1080p_example.json 中的关键取值，用于“加载示例后不被覆盖”的断言
EXAMPLE_INVOKE_ORDER = ["C", "B"]
EXAMPLE_KEY_REGION = "C"


@pytest.fixture
def builder() -> PlanBuilder:
    """A/B/C 三个区域，初始均为抄录模式。"""

    plan = PlanBuilder(1920, 1080)
    assert plan.add_region([212, 300, 620, 930]) == "A"
    assert plan.add_region([660, 440, 1460, 930]) == "B"
    assert plan.add_region([660, 330, 1460, 430]) == "C"
    return plan


def test_set_mode_resets_whole_config_to_mode_defaults(builder: PlanBuilder) -> None:
    """切到短信模式后，必须整套等于默认配置；切回抄录也要换新。"""

    builder.set_mode("A", MODE_SMS)
    assert builder.config("A") == default_sms_config()

    builder.set_mode("A", MODE_TRANSCRIBE)
    assert builder.config("A") == default_transcribe_config()


def test_set_mode_drops_stale_custom_values(builder: PlanBuilder) -> None:
    """短信模式里改过的值，切到抄录模式后不得残留（正是“旧值覆盖新配置”的等价物）。"""

    builder.set_mode("A", MODE_SMS)
    builder.update_config("A", {"scroll_distance": -999, "invoke_regions": ["B", "C"]})
    builder.set_mode("A", MODE_TRANSCRIBE)
    cfg = builder.config("A")
    # 短信模式专属字段与自定义值都必须消失
    assert "invoke_regions" not in cfg
    assert cfg["scrollable"] is False
    assert cfg["scroll_distance"] is None
    assert cfg == default_transcribe_config()


def test_existing_sms_invoke_target_is_left_alone_by_noop_mode_switch(
    builder: PlanBuilder,
) -> None:
    """把区域切到它当前已有的模式是空操作，不得动其它区域的配置。"""

    builder.set_mode("A", MODE_SMS)
    builder.update_config("A", {"invoke_regions": ["B", "C"], "key_region": "C"})

    builder.set_mode("B", MODE_TRANSCRIBE)  # B 本就是抄录，属空操作
    assert builder.config("A")["invoke_regions"] == ["B", "C"]
    assert builder.config("A")["key_region"] == "C"


def test_set_mode_sms_cleans_dangling_references(builder: PlanBuilder) -> None:
    """区域变成短信模式后，其它短信区域不得再调用它、也不得再拿它做键。"""

    builder.set_mode("A", MODE_SMS)
    builder.update_config("A", {"invoke_regions": ["B", "C"], "key_region": "C"})

    builder.set_mode("C", MODE_SMS)  # C 不再是抄录区域
    assert builder.config("A")["invoke_regions"] == ["B"]
    assert builder.config("A")["key_region"] is None


def test_remove_region_cleans_sms_references(builder: PlanBuilder) -> None:
    """删除区域走的是 ``remove_region``，这条路径的清理是正确的（对照组）。"""

    builder.set_mode("A", MODE_SMS)
    builder.update_config("A", {"invoke_regions": ["B", "C"], "key_region": "C"})
    builder.remove_region("C")
    assert builder.config("A")["invoke_regions"] == ["B"]
    assert builder.config("A")["key_region"] is None


def test_remove_region_cleans_references_and_output(builder: PlanBuilder) -> None:
    builder.set_mode("A", MODE_SMS)
    builder.update_config("A", {"invoke_regions": ["B", "C"], "key_region": "C"})
    builder.set_output_regions("C", "B")

    builder.remove_region("C")
    assert builder.config("A")["invoke_regions"] == ["B"]
    assert builder.config("A")["key_region"] is None
    # 只有被删除的区域自身的输出映射被清空；仍存在的 B 不受影响
    assert builder.title_region is None
    assert builder.desc_region == "B"


def test_invoke_region_order_is_preserved(builder: PlanBuilder) -> None:
    """“按顺序调用”是配置语义的一部分，不能被集合化打乱。"""

    builder.set_mode("A", MODE_SMS)
    builder.update_config("A", {"invoke_regions": ["C", "B"]})
    assert builder.config("A")["invoke_regions"] == ["C", "B"]


def test_loading_example_config_yields_example_values(builder: PlanBuilder) -> None:
    """复刻 UI 的“加载示例配置”：加载后 configs 必须就是文件内容。

    UI 侧只要如实展示 builder，就不会再出现“界面仍旧值”的问题。
    """

    from recruiting_capture.models.plan_io import load_plan

    root = __import__("pathlib").Path(__file__).resolve().parents[1]
    sample = load_plan(root / "configs" / "boss_1080p_example.json")

    loaded = PlanBuilder.from_plan(sample)
    loaded.screen = builder.screen
    loaded.regions = dict(sample.regions)
    loaded.configs = {name: cfg.to_dict() for name, cfg in sample.region_configs.items()}
    loaded.title_region = sample.output.title_region
    loaded.desc_region = sample.output.desc_region

    cfg_a = loaded.config("A")
    assert cfg_a["mode"] == MODE_SMS
    assert cfg_a["invoke_regions"] == EXAMPLE_INVOKE_ORDER
    assert cfg_a["key_region"] == EXAMPLE_KEY_REGION
    assert loaded.title_region == "C"
    assert loaded.desc_region == "B"
    assert loaded.config("B")["scrollable"] is True
    assert loaded.config("C")["scrollable"] is False
