"""中断暂停/继续/结束的文件控制通道。

子进程在可恢复错误时写 ``interrupt_status.json``，然后轮询
``interrupt_decision.json``。前端（Streamlit）读状态、写决定，不依赖
stdin/stdout 或额外服务。
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

STATUS_FILENAME = "interrupt_status.json"
DECISION_FILENAME = "interrupt_decision.json"
DEFAULT_PAUSE_TIMEOUT_SECONDS = 30 * 60
POLL_SECONDS = 0.5


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temp, path)


def write_status(
    run_dir: str | Path,
    state: str,
    message: str = "",
    *,
    saved: int = 0,
) -> Path:
    path = Path(run_dir) / STATUS_FILENAME
    _atomic_write_json(
        path,
        {
            "state": state,
            "message": message,
            "saved": int(saved),
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        },
    )
    return path


def read_status(run_dir: str | Path) -> dict[str, Any] | None:
    path = Path(run_dir) / STATUS_FILENAME
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


def write_decision(run_dir: str | Path, action: str) -> Path:
    if action not in {"resume", "stop"}:
        raise ValueError(f"不支持的继续/结束动作：{action!r}")
    path = Path(run_dir) / DECISION_FILENAME
    _atomic_write_json(
        path,
        {
            "action": action,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        },
    )
    return path


def clear_decision(run_dir: str | Path) -> None:
    try:
        (Path(run_dir) / DECISION_FILENAME).unlink()
    except FileNotFoundError:
        pass


def wait_for_decision(
    run_dir: str | Path,
    *,
    timeout_seconds: float = DEFAULT_PAUSE_TIMEOUT_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> str:
    """等待前端写 resume/stop；超时返回 ``timeout``。"""

    path = Path(run_dir) / DECISION_FILENAME
    deadline = clock() + max(0.0, float(timeout_seconds))
    while True:
        if path.is_file():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                payload = {}
            action = payload.get("action")
            try:
                path.unlink()
            except OSError:
                pass
            if action in {"resume", "stop"}:
                return action
        if clock() >= deadline:
            return "timeout"
        sleep(POLL_SECONDS)
