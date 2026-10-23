"""根据计划创建驱动、OCR 与大模型实现。"""

from __future__ import annotations

import logging
from pathlib import Path

from .capture.driver import ScreenDriver
from .capture.mock_driver import MockScreenDriver
from .llm.client import LLMClient
from .llm.mock_client import MockLLMClient
from .models.config import CapturePlan
from .ocr.engine import OcrEngine
from .ocr.mock_engine import MockOcrEngine

logger = logging.getLogger(__name__)


def resolve_project_path(project_root: str | Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else Path(project_root) / path


def build_llm(plan: CapturePlan) -> LLMClient:
    """按计划构建大模型客户端（主线程与 worker 共用同一份配置）。"""

    if plan.run.llm == "deepseek":
        from .llm.deepseek_client import DeepSeekClient

        llm: LLMClient = DeepSeekClient(
            base_url=plan.run.llm_base_url,
            model=plan.run.llm_model,
            timeout=plan.run.llm_timeout_seconds,
            max_retries=plan.run.llm_max_retries,
        )
        logger.info("使用 DeepSeek 视觉大模型：%s", plan.run.llm_model)
    else:
        llm = MockLLMClient()
        logger.info("使用 mock 视觉大模型（确定性规则）")
    return llm


def build_engines(
    plan: CapturePlan,
    project_root: str | Path,
) -> tuple[ScreenDriver, OcrEngine, LLMClient]:
    """按计划中的 engine/llm 选项构建真实或 mock 引擎。"""

    if plan.run.engine == "real":
        from .capture.pyautogui_driver import PyAutoGuiDriver

        driver: ScreenDriver = PyAutoGuiDriver()
        logger.info("使用真实屏幕驱动（pyautogui）")
    else:
        scenario_path = resolve_project_path(project_root, plan.run.mock_scenario)
        driver = MockScreenDriver.from_file(
            scenario_path,
            plan.regions,
            screen_size=(plan.screen.width, plan.screen.height),
            px_per_scroll_unit=plan.run.px_per_scroll_unit,
        )
        logger.info("使用 mock 屏幕驱动，场景：%s", scenario_path)

    if plan.run.engine == "real":
        from .ocr.paddle_engine import PaddleOcrEngine

        ocr: OcrEngine = PaddleOcrEngine()
        logger.info("使用 PaddleOCR 真实识别")
    else:
        ocr = MockOcrEngine()
        logger.info("使用 mock OCR（读取场景文本）")

    llm = build_llm(plan)

    return driver, ocr, llm
