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


def build_driver(plan: CapturePlan, project_root: str | Path) -> ScreenDriver:
    """按计划构建屏幕驱动（真实驱动在此注入拟人化配置）。"""

    if plan.run.engine == "real":
        from .capture.pyautogui_driver import PyAutoGuiDriver

        humanize = plan.run.humanize
        driver: ScreenDriver = PyAutoGuiDriver(
            # enabled=false 时显式关闭；true 时允许环境变量统一兜底（测试可一键关）
            humanize=None if humanize.enabled else False,
            human=humanize.to_profile(),
            idle_wander=humanize.idle_wander,
        )
        humanized = bool(getattr(driver, "humanized", humanize.enabled))
        logger.info(
            "使用真实屏幕驱动（pyautogui，拟人化=%s，空闲微动=%s）",
            "开启" if humanized else "关闭",
            "开启" if humanized and humanize.idle_wander else "关闭",
        )
        return driver

    scenario_path = resolve_project_path(project_root, plan.run.mock_scenario)
    mock: ScreenDriver = MockScreenDriver.from_file(
        scenario_path,
        plan.regions,
        screen_size=(plan.screen.width, plan.screen.height),
        px_per_scroll_unit=plan.run.px_per_scroll_unit,
    )
    logger.info("使用 mock 屏幕驱动，场景：%s", scenario_path)
    return mock


def close_driver(driver: ScreenDriver | None) -> None:
    """尽力关闭驱动（真实驱动的空闲微动线程必须显式收尾）。"""

    if driver is None:
        return
    closer = getattr(driver, "close", None)
    if not callable(closer):
        return
    try:
        closer()
    except Exception:  # pragma: no cover - 收尾失败不应覆盖主流程结果
        logger.exception("关闭屏幕驱动失败")


def build_engines(
    plan: CapturePlan,
    project_root: str | Path,
) -> tuple[ScreenDriver, OcrEngine, LLMClient]:
    """按计划中的 engine/llm 选项构建真实或 mock 引擎。"""

    driver = build_driver(plan, project_root)
    try:
        if plan.run.engine == "real":
            from .ocr.paddle_engine import PaddleOcrEngine

            ocr: OcrEngine = PaddleOcrEngine()
            logger.info("使用 PaddleOCR 真实识别")
        else:
            ocr = MockOcrEngine()
            logger.info("使用 mock OCR（读取场景文本）")

        llm = build_llm(plan)
    except Exception:
        # OCR/大模型构建失败时别把已启动的驱动线程留在后台
        close_driver(driver)
        raise

    return driver, ocr, llm
