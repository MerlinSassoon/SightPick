"""大模型异步执行：单队列 + 固定线程池 + 占位符结算。

设计要点见 docs/AMBIGUITIES.md D23 / D24。
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from ..capture.driver import Frame
from ..errors import LLMError, NonRetryableLLMError
from ..modes.denoise_ops import apply_expected_format, rule_based_result
from .client import LLMClient

logger = logging.getLogger(__name__)

_UNRESOLVED = object()  # resolve() 超时哨兵；用身份比较，不可用 ==


class PendingText:
    """去噪结果的占位符：既是槽位也是句柄。

    - 由主线程 ``LLMWorker.submit`` 创建并立即返回；
    - 由 worker 线程 ``fulfill`` 写入；
    - 由主线程在屏障处 ``resolve`` 读取。
    """

    __slots__ = (
        "seq",
        "region",
        "payload",
        "_event",
        "_value",
        "_warnings",
        "_error",
        "_declared",
    )

    def __init__(self, seq: int, region: str, payload: list[str] | None = None) -> None:
        self.seq = seq
        self.region = region
        self.payload: list[str] = list(payload or [])
        self._event = threading.Event()
        self._value: Any = None
        self._warnings: list[str] = []
        self._error: BaseException | None = None
        self._declared = False

    @property
    def is_resolved(self) -> bool:
        return self._declared

    def fulfill(
        self,
        value: Any,
        warnings: list[str] | None = None,
        error: BaseException | None = None,
    ) -> None:
        """幂等写入：先写者赢。"""
        if self._declared:
            return
        self._declared = True
        self._value = value
        self._warnings = list(warnings or [])
        self._error = error
        self._event.set()

    def resolve(self, timeout: float) -> tuple[Any, list[str]]:
        """等待并返回 ``(value, warnings)``。

        - 正常返回已套用期望格式的结果；
        - 超时返回 ``_UNRESOLVED``（调用方必须兜底）；
        - **永不抛异常**。
        """
        if not self._event.wait(timeout=max(0.0, timeout)):
            return _UNRESOLVED, [
                f"异步去噪等待超时（>{timeout:.0f}s），已按规则清理"
            ]
        return self._value, list(self._warnings)


@dataclass(frozen=True)
class DenoiseTask:
    """一次去噪请求的不可变快照。"""

    texts: list[str]
    frame: Frame | None
    expected_content: str
    expected_format: str
    region: str
    attempts: int
    handle: PendingText = field(compare=False)
    context: dict[str, Any] = field(default_factory=dict)


class LLMWorker:
    """固定大小的后台线程池，按队列顺序执行去噪任务。"""

    def __init__(
        self,
        client: LLMClient,
        *,
        threads: int = 2,
        queue_max: int = 8,
        task_retries: int = 1,
        retry_wait_seconds: float = 2.0,
        sleep=time.sleep,
    ) -> None:
        self._client = client
        self._core_threads = max(1, int(threads))
        self._queue: queue.Queue[DenoiseTask] = queue.Queue(maxsize=max(1, int(queue_max)))
        self._closing = threading.Event()
        self._sleep = sleep
        self._task_retries = max(0, int(task_retries))
        self._retry_wait = max(0.0, float(retry_wait_seconds))
        self._lock = threading.Lock()
        self._seq = 0
        self._handles: list[PendingText] = []
        self.total_calls = 0
        self._abandoned: list[PendingText] = []
        self._threads: list[threading.Thread] = [
            threading.Thread(target=self._run, daemon=True, name=f"llm-worker-{i}")
            for i in range(self._core_threads)
        ]
        for thread in self._threads:
            thread.start()

    # ------------------------------------------------------------------ #
    # 提交
    # ------------------------------------------------------------------ #
    def submit(
        self,
        *,
        texts: list[str],
        frame: Frame | None,
        expected_content: str,
        expected_format: str,
        region: str,
    ) -> PendingText:
        payload = list(texts)
        with self._lock:
            self._seq += 1
            handle = PendingText(self._seq, region, payload)
            self._handles.append(handle)
        task = DenoiseTask(
            texts=payload,
            frame=frame,
            expected_content=expected_content,
            expected_format=expected_format,
            region=region,
            attempts=self._task_retries + 1,
            handle=handle,
            context={"region": region},
        )
        while True:
            if self._closing.is_set():
                handle.fulfill(
                    rule_based_result(texts, expected_format),
                    ["队列已关闭，未提交大模型；已按规则清理"],
                )
                return handle
            try:
                self._queue.put(task, timeout=0.5)
                logger.debug("提交去噪任务 seq=%d region=%s", handle.seq, region)
                return handle
            except queue.Full:
                continue

    # ------------------------------------------------------------------ #
    # 执行
    # ------------------------------------------------------------------ #
    def _run(self) -> None:
        while True:
            try:
                task = self._queue.get(timeout=0.2)
            except queue.Empty:
                if self._closing.is_set():
                    return
                continue
            try:
                self._execute(task)
            except BaseException as exc:  # 线程绝不允许因异常退出
                logger.exception("LLM worker 未预期异常（region=%s）", task.region)
                task.handle.fulfill(
                    rule_based_result(task.texts, task.expected_format),
                    [f"worker 内部异常：{exc}"],
                )
            finally:
                self._queue.task_done()

    def _execute(self, task: DenoiseTask) -> None:
        for attempt in range(1, task.attempts + 1):
            with self._lock:
                self.total_calls += 1
            # 每次尝试都清空上一轮的降级结果：重试成功后不得沿用旧值
            warnings: list[str] = []
            value: Any = None
            try:
                value = apply_expected_format(
                    self._client.denoise(
                        texts=task.texts,
                        frame=task.frame,
                        expected_content=task.expected_content,
                        expected_format=task.expected_format,
                        context=dict(task.context),
                    ),
                    task.texts,
                    task.expected_format,
                )
            except NonRetryableLLMError as exc:
                # 重试也必然失败：立即按 R6 降级，不再消耗剩余次数（C-4）
                value = rule_based_result(task.texts, task.expected_format)
                warnings = [f"去噪调用大模型失败，按 R6 降级为规则清理：{exc}"]
                task.handle.fulfill(value, warnings)
                return
            except LLMError as exc:
                value = rule_based_result(task.texts, task.expected_format)
                warnings = [f"去噪调用大模型失败，按 R6 降级为规则清理：{exc}"]
                if attempt < task.attempts:
                    logger.warning(
                        "区域 %s 去噪失败（第 %d/%d 次），%.1fs 后重试",
                        task.region,
                        attempt,
                        task.attempts,
                        self._retry_wait,
                    )
                    self._sleep(self._retry_wait)
                    continue
                task.handle.fulfill(value, warnings)
                return
            task.handle.fulfill(value, warnings)
            return

    # ------------------------------------------------------------------ #
    # 收尾
    # ------------------------------------------------------------------ #
    def close(self, *, drain: bool = True, timeout: float = 30.0) -> list[PendingText]:
        """停止线程池。

        ``drain=True``：先排空队列（正常路径）；
        ``drain=False``：立即放弃（异常/急停路径，避免拖慢退出）。
        返回仍未完成的句柄（已全部兜底，调用方可记录数量）。
        """
        self._closing.set()
        if drain:
            deadline = time.monotonic() + max(0.0, float(timeout))
            for thread in self._threads:
                thread.join(timeout=max(0.0, deadline - time.monotonic()))
        with self._lock:
            pending = [h for h in self._handles if not h.is_resolved]
        for handle in pending:
            handle.fulfill(
                None,
                ["异步去噪未完成，已放弃"],
            )
        self._abandoned = pending
        return pending
