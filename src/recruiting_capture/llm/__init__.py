"""大模型层导出。"""

from .client import AnchorConfirmation, ClickConfirmation, LLMClient
from .mock_client import MockLLMClient

__all__ = [
    "AnchorConfirmation",
    "ClickConfirmation",
    "LLMClient",
    "MockLLMClient",
]


def __getattr__(name: str):
    if name == "DeepSeekClient":
        from .deepseek_client import DeepSeekClient

        return DeepSeekClient
    raise AttributeError(name)
