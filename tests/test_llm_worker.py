"""LLMWorker / PendingText 的并发语义（W1-W7）。

约束：
- 假客户端用 threading.Event 控制阻塞，不用 sleep 猜时序；
- 断言一律 resolve(timeout=5)，防止 CI 挂死；
- 每个测试结束 worker.close(drain=False)，避免线程泄漏；
- 不构造 Frame（image 是 ndarray，dataclass 相等比较会抛 ValueError），统一 frame=None。
"""

from __future__ import annotations

import threading
import time

from recruiting_capture.errors import LLMError, NonRetryableLLMError
from recruiting_capture.llm.client import AnchorConfirmation, ClickConfirmation
from recruiting_capture.llm.worker import LLMWorker, PendingText, _UNRESOLVED

TIMEOUT = 5.0


class RecordingLLM:
    """记录调用次数，按预置脚本返回或抛错。"""

    def __init__(self, outcomes=None) -> None:
        self.calls = 0
        self._outcomes = list(outcomes or [])
        self._lock = threading.Lock()

    def denoise(self, *, texts, frame, expected_content, expected_format, context=None):
        with self._lock:
            self.calls += 1
            index = self.calls - 1
        outcome = self._outcomes[index] if index < len(self._outcomes) else ["默认"]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def confirm_anchor(self, *, texts, frame, anchor, context=None):
        return AnchorConfirmation(True)

    def confirm_click(self, *, texts, frame, context=None):
        return ClickConfirmation(True)


class GatedLLM(RecordingLLM):
    """在 denoise 里阻塞，直到测试放行。"""

    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def denoise(self, *, texts, frame, expected_content, expected_format, context=None):
        self.entered.set()
        assert self.release.wait(timeout=TIMEOUT)
        return super().denoise(
            texts=texts,
            frame=frame,
            expected_content=expected_content,
            expected_format=expected_format,
            context=context,
        )


def _worker(client, **kwargs) -> LLMWorker:
    params = {"threads": 1, "queue_max": 8, "task_retries": 1, "retry_wait_seconds": 0.0}
    params.update(kwargs)
    return LLMWorker(client, sleep=lambda seconds: None, **params)


def _submit(worker, *, texts=None, expected_format="list", region="B"):
    return worker.submit(
        texts=list(texts or ["行1", "行2"]),
        frame=None,
        expected_content="职位描述",
        expected_format=expected_format,
        region=region,
    )


# W1 ------------------------------------------------------------------ #
def test_submit_then_resolve_applies_format() -> None:
    worker = _worker(RecordingLLM([["a", "b", "a"]]))
    try:
        handle = _submit(worker)
        value, warnings = handle.resolve(TIMEOUT)
        assert warnings == []
        assert value == ["a", "b"]
        assert handle.is_resolved
    finally:
        worker.close(drain=False)


# W2 ------------------------------------------------------------------ #
def test_backpressure_blocks_then_succeeds() -> None:
    client = GatedLLM()
    worker = _worker(client, threads=1, queue_max=1)
    try:
        first = _submit(worker)
        assert client.entered.wait(TIMEOUT)
        second = _submit(worker)  # 队列有 1 个空位，立即成功
        third_results: list[PendingText] = []

        def slow_submit() -> None:
            third_results.append(_submit(worker, texts=["第三批"]))

        thread = threading.Thread(target=slow_submit, daemon=True)
        thread.start()
        # 队列已满，submit 应阻塞
        thread.join(timeout=0.3)
        assert thread.is_alive(), "队列满时 submit 应当阻塞"

        client.release.set()
        thread.join(timeout=TIMEOUT)
        assert not thread.is_alive(), "放行后 submit 应当完成"
        assert len(third_results) == 1

        for handle in (first, second, third_results[0]):
            value, warnings = handle.resolve(TIMEOUT)
            assert warnings == []
            assert value
    finally:
        client.release.set()
        worker.close(drain=False)


# W3 ------------------------------------------------------------------ #
def test_retryable_failure_then_success() -> None:
    client = RecordingLLM([LLMError("瞬时故障"), ["恢复"]])
    worker = _worker(client, task_retries=1)
    try:
        handle = _submit(worker)
        value, warnings = handle.resolve(TIMEOUT)
        assert worker.total_calls == 2
        assert warnings == []
        assert value == ["恢复"]
    finally:
        worker.close(drain=False)


# W4 ------------------------------------------------------------------ #
def test_non_retryable_failure_calls_once_and_degrades() -> None:
    client = RecordingLLM([NonRetryableLLMError("HTTP 400")])
    worker = _worker(client, task_retries=1)
    try:
        handle = _submit(worker, texts=["  职位描述  ", "岗位职责一"])
        value, warnings = handle.resolve(TIMEOUT)
        assert worker.total_calls == 1, "不可重试失败不应消耗重试次数"
        assert warnings and "降级" in warnings[0]
        assert value, "降级后应仍有规则清理结果"
    finally:
        worker.close(drain=False)


# W5 ------------------------------------------------------------------ #
def test_timeout_then_idempotent_fulfill() -> None:
    handle = PendingText(1, "B", ["原始行"])
    value, warnings = handle.resolve(0)
    assert value is _UNRESOLVED
    assert warnings and "超时" in warnings[0]
    assert not handle.is_resolved
    assert handle.payload == ["原始行"]

    handle.fulfill(["正式结果"])
    assert handle.is_resolved
    assert handle.resolve(TIMEOUT) == (["正式结果"], [])

    handle.fulfill(["迟到结果"], ["迟到告警"])
    assert handle.resolve(TIMEOUT) == (["正式结果"], []), "先写者赢"


# W6 ------------------------------------------------------------------ #
def test_internal_exception_does_not_kill_thread() -> None:
    class BoomLLM(RecordingLLM):
        def denoise(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("非 LLMError 的内部错误")
            return ["正常"]

    client = BoomLLM()
    worker = _worker(client, task_retries=1)
    try:
        first = _submit(worker)
        value, warnings = first.resolve(TIMEOUT)
        assert warnings and "worker 内部异常" in warnings[0]
        assert value is not None

        second = _submit(worker)
        value, warnings = second.resolve(TIMEOUT)
        assert value == ["正常"], "线程必须继续处理后续任务"
    finally:
        worker.close(drain=False)


# W7 ------------------------------------------------------------------ #
def test_close_drain_settles_all_handles() -> None:
    client = RecordingLLM()
    worker = _worker(client, threads=2)
    handles = [_submit(worker, texts=[f"行{i}"]) for i in range(4)]
    abandoned = worker.close(drain=True, timeout=TIMEOUT)
    assert abandoned == []
    for handle in handles:
        assert handle.is_resolved
        value, _ = handle.resolve(TIMEOUT)
        assert value is not None, "正常路径不应把 None 泄漏到结果"


def test_close_without_drain_settles_pending() -> None:
    client = GatedLLM()
    worker = _worker(client, threads=1)
    handle = _submit(worker)
    assert client.entered.wait(TIMEOUT)
    abandoned = worker.close(drain=False)
    assert abandoned == [handle]
    assert handle.is_resolved


def test_submit_after_close_degrades_immediately() -> None:
    client = GatedLLM()
    worker = _worker(client, threads=1, queue_max=1)
    _submit(worker)
    assert client.entered.wait(TIMEOUT)
    _submit(worker)  # 占满队列
    worker.close(drain=False)
    client.release.set()

    handle = _submit(worker)  # 队列满且已关闭 → 立即降级
    assert handle.is_resolved
    value, warnings = handle.resolve(0)
    assert warnings and "队列已关闭" in warnings[0]
    assert value is not None


def test_worker_threads_are_daemon() -> None:
    worker = _worker(RecordingLLM(), threads=2)
    try:
        assert worker._threads and all(t.daemon for t in worker._threads)
    finally:
        worker.close(drain=False)
