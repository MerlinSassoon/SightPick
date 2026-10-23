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


class NonRetryableLLMError(LLMError):
    """重试无意义的失败（如 4xx 非 429）。

    客户端与异步 worker 据此区分“可重试的瞬时故障”与“重试也必然失败”。
    """


class AnchorNotFoundError(RecruitingError):
    """首轮未找到语义锚点（如“职位描述”），且视觉大模型确认不存在。"""


class ScreenChangedError(RecruitingError):
    """运行环境屏幕尺寸与配置时不一致（R1）。"""


class AutomationAbortedError(RecruitingError):
    """自动化无法继续：急停、pyautogui 失效保护，或输入注入被系统拒绝。"""


class InputInjectionError(AutomationAbortedError):
    """当前解释器/进程无法向桌面注入鼠标或键盘输入。

    Windows 上 ``SetCursorPos``/``mouse_event`` 在缺少输入注入权限时**返回失败但不抛异常**，
    调用方若不校验返回值就会“日志一切正常、鼠标纹丝不动”。本异常用于把这种
    静默失败显式暴露出来。
    """


class StopRequested(RecruitingError):
    """外部请求停止（保留给前端/CLI 的停止按钮）。"""
