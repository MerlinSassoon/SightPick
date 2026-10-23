"""两类模式的工作流程（mock 驱动/OCR/大模型）。"""

from __future__ import annotations

import pytest

from recruiting_capture.capture.mock_driver import MockScreenDriver
from recruiting_capture.errors import AnchorNotFoundError, LLMError
from recruiting_capture.llm.client import AnchorConfirmation, ClickConfirmation
from recruiting_capture.llm.mock_client import MockLLMClient
from recruiting_capture.modes.base import ModeContext
from recruiting_capture.modes.short_message import ShortMessageMode
from recruiting_capture.modes.transcribe import TranscribeMode
from recruiting_capture.models.config import SmsConfig, TranscribeConfig
from recruiting_capture.ocr.mock_engine import MockOcrEngine
from recruiting_capture.text_utils import is_noise_line


def _context(plan, scenario, llm=None, driver=None):
    driver = driver or MockScreenDriver(
        scenario,
        plan.regions,
        screen_size=(plan.screen.width, plan.screen.height),
    )
    context = ModeContext(
        driver=driver,
        ocr=MockOcrEngine(),
        llm=llm or MockLLMClient(),
        run=plan.run,
        geometry=plan.geometry,
        sleep=lambda seconds: None,
    )
    return context, driver


def test_description_workflow_collects_clean_unique_lines(demo_plan, scenario) -> None:
    context, driver = _context(demo_plan, scenario)
    mode = TranscribeMode(demo_plan.regions["B"], demo_plan.region_configs["B"], context)
    result = mode.workflow()

    assert isinstance(result, list)
    assert len(result) >= 15
    assert "职位描述" not in result
    assert not any("工作地址" in line for line in result)
    assert not any(is_noise_line(line) for line in result)
    assert len(result) == len(set(result))
    assert mode.stop_marker == "工作地址"
    assert mode.rounds_used >= 2
    assert len([event for event in driver.scroll_events if event[0] == "B"]) >= 2


def test_title_workflow_returns_job_title(demo_plan, scenario) -> None:
    context, _ = _context(demo_plan, scenario)
    mode = TranscribeMode(demo_plan.regions["C"], demo_plan.region_configs["C"], context)
    result = mode.workflow()
    assert result == "中高级开发工程师（20-40k*14-16薪）"


def test_tuple_format(demo_plan, scenario) -> None:
    config = TranscribeConfig.from_dict(demo_plan.region_configs["C"].to_dict())
    config.expected_format = "tuple"
    context, _ = _context(demo_plan, scenario)
    mode = TranscribeMode(demo_plan.regions["C"], config, context)
    result = mode.workflow()
    assert isinstance(result, tuple)
    assert "中高级开发工程师（20-40k*14-16薪）" in result


def test_anchor_not_found_raises(demo_plan, scenario) -> None:
    class NoAnchorLLM(MockLLMClient):
        def confirm_anchor(self, *, texts, frame, anchor, context=None):
            return AnchorConfirmation(False, "stub: 页面不存在锚点")

    config = TranscribeConfig.from_dict(demo_plan.region_configs["B"].to_dict())
    config.anchor_key = "不存在的锚点"
    context, _ = _context(demo_plan, scenario, llm=NoAnchorLLM())
    mode = TranscribeMode(demo_plan.regions["B"], config, context)
    with pytest.raises(AnchorNotFoundError):
        mode.workflow()


def test_anchor_correction_is_logged_and_suggests_scroll(demo_plan, scenario) -> None:
    class CorrectedLLM(MockLLMClient):
        def confirm_anchor(self, *, texts, frame, anchor, context=None):
            return AnchorConfirmation(
                True,
                "stub: OCR 错字",
                corrected_key="职位描述",
                box=[10, 400, 200, 430],
            )

    config = TranscribeConfig.from_dict(demo_plan.region_configs["B"].to_dict())
    config.anchor_key = "职位描迷"
    context, _ = _context(demo_plan, scenario, llm=CorrectedLLM())
    mode = TranscribeMode(demo_plan.regions["B"], config, context)
    result = mode.workflow()
    assert result
    assert any("建议滚动距离" in warning for warning in mode.warnings)


def test_llm_failure_degrades_to_rules(demo_plan, scenario) -> None:
    class FailingLLM(MockLLMClient):
        def denoise(self, **kwargs):
            raise LLMError("stub: 接口失败")

    context, _ = _context(demo_plan, scenario, llm=FailingLLM())
    mode = TranscribeMode(demo_plan.regions["B"], demo_plan.region_configs["B"], context)
    result = mode.workflow()
    assert isinstance(result, list) and result
    assert any("降级" in warning for warning in mode.warnings)


def test_sms_dict_mode_and_records(demo_plan, scenario) -> None:
    context, _ = _context(demo_plan, scenario)
    modes = {
        name: TranscribeMode(demo_plan.regions[name], demo_plan.region_configs[name], context)
        for name in ("B", "C")
    }
    sms = ShortMessageMode(
        demo_plan.regions["A"], demo_plan.region_configs["A"], context, modes
    )
    raw = sms.workflow()
    assert set(raw) == {"A"}
    assert isinstance(raw["A"], dict)
    assert len(raw["A"]) == 3
    assert sms.stop_reason == "list_end"
    records = sms.to_records(demo_plan.output)
    assert len(records) == 3
    assert all(record["job_desc"] for record in records)
    assert records[0]["job_title"] == "中高级开发工程师（20-40k*14-16薪）"


def test_sms_set_mode(demo_plan, scenario) -> None:
    config = SmsConfig.from_dict(demo_plan.region_configs["A"].to_dict())
    config.data_structure = "set"
    config.key_region = None
    context, _ = _context(demo_plan, scenario)
    modes = {
        name: TranscribeMode(demo_plan.regions[name], demo_plan.region_configs[name], context)
        for name in ("B", "C")
    }
    sms = ShortMessageMode(demo_plan.regions["A"], config, context, modes)
    raw = sms.workflow()
    outer = raw["A"]
    assert isinstance(outer, list) and len(outer) == 3
    assert isinstance(outer[0], list) and len(outer[0]) == 2
    assert isinstance(outer[0][0], str)
    assert isinstance(outer[0][1], list)


def test_click_verification_rolls_back_then_succeeds(demo_plan, scenario) -> None:
    class FlakyClickLLM(MockLLMClient):
        def __init__(self) -> None:
            super().__init__()
            self.click_calls = 0

        def confirm_click(self, *, texts, frame, context=None):
            self.click_calls += 1
            return ClickConfirmation(self.click_calls > 1, "stub: 首次未命中")

    llm = FlakyClickLLM()
    config = SmsConfig.from_dict(demo_plan.region_configs["A"].to_dict())
    config.verify_click = True
    config.max_items = 1
    context, driver = _context(demo_plan, scenario, llm=llm)
    modes = {
        name: TranscribeMode(demo_plan.regions[name], demo_plan.region_configs[name], context)
        for name in ("B", "C")
    }
    sms = ShortMessageMode(demo_plan.regions["A"], config, context, modes)
    sms.workflow()
    assert llm.click_calls >= 2
    assert any(label == "A" and amount > 0 for label, amount in driver.scroll_events)
    assert len(sms.items) == 1


def test_sms_max_items_stop(demo_plan, scenario) -> None:
    config = SmsConfig.from_dict(demo_plan.region_configs["A"].to_dict())
    config.max_items = 2
    context, _ = _context(demo_plan, scenario)
    modes = {
        name: TranscribeMode(demo_plan.regions[name], demo_plan.region_configs[name], context)
        for name in ("B", "C")
    }
    sms = ShortMessageMode(demo_plan.regions["A"], config, context, modes)
    sms.workflow()
    assert len(sms.items) == 2
    assert sms.stop_reason == "max_items"
