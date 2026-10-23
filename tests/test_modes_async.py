"""异步路径与同步路径的等价性（A1/A3/A4/A5）。"""

from __future__ import annotations

from recruiting_capture.capture.mock_driver import MockScreenDriver
from recruiting_capture.errors import LLMError, NonRetryableLLMError
from recruiting_capture.llm.mock_client import MockLLMClient
from recruiting_capture.modes.base import ModeContext
from recruiting_capture.modes.short_message import ShortMessageMode
from recruiting_capture.modes.transcribe import TranscribeMode
from recruiting_capture.ocr.mock_engine import MockOcrEngine

TITLES = [
    "中高级开发工程师（20-40k*14-16薪）",
    "数据开发工程师（自研&双休）",
    "python开发工程师",
]


class FailingDenoiseLLM(MockLLMClient):
    """让指定区域的前 N 次去噪失败，其后正常。"""

    def __init__(
        self, fail_region: str, fail_first_calls: int = 1, error_type: type = LLMError
    ) -> None:
        super().__init__()
        self.fail_region = fail_region
        self.fail_first_calls = fail_first_calls
        self.error_type = error_type
        self.calls = 0
        self._failures = 0

    def denoise(self, *, texts, frame, expected_content, expected_format, context=None):
        self.calls += 1
        region = (context or {}).get("region")
        if region == self.fail_region and self._failures < self.fail_first_calls:
            self._failures += 1
            raise self.error_type(f"stub: 区域 {region} 失败")
        return super().denoise(
            texts=texts,
            frame=frame,
            expected_content=expected_content,
            expected_format=expected_format,
            context=context,
        )


def _context(plan, scenario, *, llm=None, factory=None):
    driver = MockScreenDriver(
        scenario,
        plan.regions,
        screen_size=(plan.screen.width, plan.screen.height),
    )
    return ModeContext(
        driver=driver,
        ocr=MockOcrEngine(),
        llm=llm or MockLLMClient(),
        run=plan.run,
        geometry=plan.geometry,
        sleep=lambda seconds: None,
        llm_factory=factory,
    )


def _sms(plan, context, *, sms_config=None):
    modes = {
        name: TranscribeMode(plan.regions[name], plan.region_configs[name], context)
        for name in ("B", "C")
    }
    return ShortMessageMode(
        plan.regions["A"], sms_config or plan.region_configs["A"], context, modes
    )


def _walk(value):
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk(item)


# A1 ------------------------------------------------------------------ #
def test_placeholder_never_escapes(demo_plan, scenario) -> None:
    from recruiting_capture.llm.worker import PendingText

    context = _context(demo_plan, scenario, factory=MockLLMClient)
    sms = _sms(demo_plan, context)
    raw = sms.workflow()
    leaked = [item for item in _walk(raw) if isinstance(item, PendingText)]
    assert leaked == []
    assert raw["A"] and all(
        isinstance(key, str) for key in raw["A"]
    ), "字典模式的键必须是字符串"


# A3 ------------------------------------------------------------------ #
def test_warning_stays_with_its_own_item(demo_plan, scenario) -> None:
    # 第一个岗位的 B 区域直接返回不可重试失败 → 只有第 1 项带降级告警
    llm = FailingDenoiseLLM("B", fail_first_calls=1, error_type=NonRetryableLLMError)
    context = _context(demo_plan, scenario, llm=llm, factory=lambda: llm)
    sms = _sms(demo_plan, context)
    sms.workflow()

    assert len(sms.items) == 3
    first = sms.items[0]
    assert any("降级" in warning for warning in first.warnings), "第 1 项应带降级告警"
    assert len(first.values["B"]) > 0, "降级后描述不得为空"
    for later in sms.items[1:]:
        assert not any("降级" in warning for warning in later.warnings), "告警不得串到其它岗位"


def test_retried_denoise_does_not_lose_content(demo_plan, scenario) -> None:
    """回归：worker 重试成功后，不得沿用上一轮的空降级结果。"""

    llm = FailingDenoiseLLM("B", fail_first_calls=2)
    context = _context(demo_plan, scenario, llm=llm, factory=lambda: llm)
    sms = _sms(demo_plan, context)
    sms.workflow()
    first = sms.items[0]
    assert first.values["B"], "第 1 项描述不得为空（重试成功后应返回真实内容）"
    assert "职位描述" not in first.values["B"]
    assert not any("降级" in warning for warning in first.warnings)


# A4 ------------------------------------------------------------------ #
def test_dict_keys_use_title_region(demo_plan, scenario) -> None:
    context = _context(demo_plan, scenario, factory=MockLLMClient)
    sms = _sms(demo_plan, context)
    raw = sms.workflow()
    assert list(raw["A"]) == TITLES
    assert "缺省" not in raw["A"]


# A5 ------------------------------------------------------------------ #
def test_sync_fallback_matches_async(demo_plan, scenario) -> None:
    async_raw = _sms(
        demo_plan, _context(demo_plan, scenario, factory=MockLLMClient)
    ).workflow()

    sync_raw = _sms(
        demo_plan, _context(demo_plan, scenario, factory=None)
    ).workflow()

    assert sync_raw == async_raw


def test_async_off_when_factory_absent(demo_plan, scenario) -> None:
    context = _context(demo_plan, scenario, factory=None)
    sms = _sms(demo_plan, context)
    sms.workflow()
    assert context.stats.get("llm_calls_async", 0) == 0
    assert context.stats.get("llm_wait_ms", 0) >= 0
