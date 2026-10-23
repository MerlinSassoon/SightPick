"""统一异常类型：保证 NFR-01（异常给出可读提示而非静默失败）。"""

from __future__ import annotations


class RecruitingError(Exception):
    """本平台所有可预期异常的基类。"""


class ConfigError(RecruitingError):
    """计划/区域/区域配置非法。"""


class OcrError(RecruitingError):
    """OCR 引擎调用失败。"""


class LLMError(RecruitingError):
    """视觉大模型接口调用或返回解析失败。"""


class AnchorNotFoundError(RecruitingError):
    """首轮未找到语义锚点（如“职位描述”），且视觉大模型确认不存在。"""


class ScreenChangedError(RecruitingError):
    """运行环境屏幕尺寸与配置时不一致（R1）。"""


class AutomationAbortedError(RecruitingError):
    """用户触发急停或 pyautogui 失效保护。"""


class StopRequested(RecruitingError):
    """外部请求停止（保留给前端/CLI 的停止按钮）。"""
