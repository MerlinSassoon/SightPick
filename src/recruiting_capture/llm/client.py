"""视觉大模型接口抽象与结果类型（NEWREADME 6.3 函数八点零、6.5.3、8.3）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from ..capture.driver import Frame


@dataclass
class AnchorConfirmation:
    """首轮锚点确认结果（8.3.2 / 6.5.3）。"""

    exists: bool
    reason: str = ""
    corrected_key: str | None = None
    box: list[int] | None = None


@dataclass
class ClickConfirmation:
    """点击是否命中新列表项的确认结果（8.2.3）。"""

    matched: bool
    reason: str = ""


@runtime_checkable
class LLMClient(Protocol):
    calls: int

    def denoise(
        self,
        *,
        texts: list[str],
        frame: Frame | None,
        expected_content: str,
        expected_format: str,
        context: dict[str, Any] | None = None,
    ) -> str | list[str] | tuple[str, ...]: ...

    def confirm_anchor(
        self,
        *,
        texts: list[str],
        frame: Frame | None,
        anchor: str,
        context: dict[str, Any] | None = None,
    ) -> AnchorConfirmation: ...

    def confirm_click(
        self,
        *,
        texts: list[str],
        frame: Frame | None,
        context: dict[str, Any] | None = None,
    ) -> ClickConfirmation: ...
