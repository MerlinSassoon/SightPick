"""DeepSeek 视觉大模型客户端（OpenAI 兼容 /chat/completions）。

官方文档：https://api-docs.deepseek.com/guides/vision/
默认模型 ``deepseek-flash``，图像以 base64 data URL 放在 user 消息中。
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import time
from typing import Any, Sequence

from ..capture.driver import Frame
from ..errors import LLMError, NonRetryableLLMError
from .client import AnchorConfirmation, ClickConfirmation
from .prompts import (
    ANCHOR_SYSTEM,
    CLICK_SYSTEM,
    build_anchor_prompt,
    build_click_prompt,
    build_denoise_prompt,
    build_denoise_system,
)

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "DeepSeek-V4.1-Flash-1017"
DEFAULT_TIMEOUT = 60
DEFAULT_RETRIES = 2


_NonRetryableLLMError = NonRetryableLLMError  # 兼容旧名；新代码请直接用公开名


def _extract_json(text: str, *, context: str = "大模型返回") -> dict[str, Any]:
    """从模型输出中提取 JSON 对象（容忍 ```json 代码块与前后解释）。"""

    if not isinstance(text, str) or not text.strip():
        raise LLMError(f"{context}为空")
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise LLMError(f"{context}不是 JSON：{cleaned[:200]!r}")
    try:
        payload = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LLMError(f"{context} JSON 解析失败：{exc}；原文：{cleaned[:200]!r}") from exc
    if not isinstance(payload, dict):
        raise LLMError(f"{context}应为 JSON 对象，收到：{type(payload).__name__}")
    return payload


def _frame_to_png_data_url(frame: Frame) -> str:
    from PIL import Image

    buffer = io.BytesIO()
    Image.fromarray(frame.image).convert("RGB").save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _as_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        result = []
        for item in value:
            text = str(item).strip()
            if text:
                result.append(text)
        return result
    raise LLMError(f"大模型返回的文本列表格式非法：{type(value).__name__}")


class DeepSeekClient:
    """视觉大模型客户端：去噪、锚点确认、点击确认，含重试与 JSON 容错。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        timeout: int = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_RETRIES,
        session: Any | None = None,
        sleep=time.sleep,
    ) -> None:
        self.api_key = (api_key or os.environ.get("DEEPSEEK_API_KEY") or "").strip()
        if not self.api_key:
            raise LLMError(
                "未配置 DeepSeek API Key。请设置环境变量 DEEPSEEK_API_KEY 后重试，"
                "或改用 --llm mock 进行离线演示。"
            )
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = int(timeout)
        self.max_retries = max(0, int(max_retries))
        self.calls = 0
        self._sleep = sleep
        if session is None:
            import requests

            session = requests.Session()
        self._session = session
        logger.info("DeepSeek 客户端就绪：base_url=%s model=%s", self.base_url, self.model)

    # ------------------------------------------------------------------ #
    # HTTP
    # ------------------------------------------------------------------ #
    def _messages(
        self, system: str, prompt: str, frame: Frame | None
    ) -> list[dict[str, Any]]:
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        if frame is not None:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": _frame_to_png_data_url(frame), "detail": "high"},
                }
            )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ]

    def _post(self, system: str, prompt: str, frame: Frame | None) -> str:
        import requests

        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": self._messages(system, prompt, frame),
            "temperature": 0,
            "max_tokens": 4096,
            "response_format": {"type": "json_object"},
            # 删除 "temperature": 0, 和顶层 "reasoning_effort": "none"
            "thinking": {"type": "disabled"},  # 👈 用这个来关闭思考
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self.calls += 1
            try:
                response = self._session.post(
                    url, headers=headers, json=payload, timeout=self.timeout
                )
                status = int(getattr(response, "status_code", 0))
                if status == 429 or status >= 500:
                    raise LLMError(f"DeepSeek 服务暂时不可用（HTTP {status}）")
                if status >= 400:
                    snippet = str(getattr(response, "text", ""))[:300]
                    raise _NonRetryableLLMError(
                        f"DeepSeek 请求失败（HTTP {status}）：{snippet}"
                    )
                data = response.json()
                choices = data.get("choices") or []
                if not choices:
                    raise LLMError(f"DeepSeek 返回缺少 choices：{str(data)[:200]}")
                message = choices[0].get("message") or {}
                content = message.get("content")
                if not isinstance(content, str) or not content.strip():
                    raise LLMError("DeepSeek 返回内容为空")
                return content
            except _NonRetryableLLMError:
                raise
            except (requests.RequestException, LLMError, ValueError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                wait = min(8.0, 1.5 * (2**attempt))
                logger.warning(
                    "DeepSeek 调用失败（第 %d/%d 次）：%s；%.1fs 后重试",
                    attempt + 1,
                    self.max_retries + 1,
                    exc,
                    wait,
                )
                self._sleep(wait)
        raise LLMError(f"DeepSeek 调用失败：{last_error}")

    def _chat_json(self, system: str, prompt: str, frame: Frame | None) -> dict[str, Any]:
        text = self._post(system, prompt, frame)
        return _extract_json(text, context="DeepSeek 返回")

    # ------------------------------------------------------------------ #
    # LLMClient 协议
    # ------------------------------------------------------------------ #
    def denoise(
        self,
        *,
        texts: list[str],
        frame: Frame | None,
        expected_content: str,
        expected_format: str,
        context: dict[str, Any] | None = None,
    ) -> str | list[str] | tuple[str, ...]:
        payload = self._chat_json(
            build_denoise_system(expected_content),
            build_denoise_prompt(texts, expected_content, expected_format),
            frame,
        )
        result = payload.get("result", payload.get("text", payload.get("data")))
        if expected_format == "str":
            if isinstance(result, str):
                return result.strip()
            lines = _as_text_list(result)
            return lines[0] if lines else ""
        lines = _as_text_list(result)
        if expected_format == "tuple":
            return tuple(lines)
        return lines

    def confirm_anchor(
        self,
        *,
        texts: list[str],
        frame: Frame | None,
        anchor: str,
        context: dict[str, Any] | None = None,
    ) -> AnchorConfirmation:
        payload = self._chat_json(
            ANCHOR_SYSTEM, build_anchor_prompt(texts, anchor), frame
        )
        corrected = payload.get("corrected_key") or payload.get("corrected")
        box = payload.get("box")
        if isinstance(box, (list, tuple)) and len(box) == 4:
            box = [int(round(float(v))) for v in box]
        else:
            box = None
        return AnchorConfirmation(
            exists=bool(payload.get("exists")),
            reason=str(payload.get("reason") or ""),
            corrected_key=str(corrected).strip() if corrected else None,
            box=box,
        )

    def confirm_click(
        self,
        *,
        texts: list[str],
        frame: Frame | None,
        context: dict[str, Any] | None = None,
    ) -> ClickConfirmation:
        payload = self._chat_json(CLICK_SYSTEM, build_click_prompt(texts), frame)
        return ClickConfirmation(
            matched=bool(payload.get("matched")),
            reason=str(payload.get("reason") or ""),
        )
