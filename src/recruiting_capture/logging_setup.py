"""日志初始化（NFR-03 可观测性）。

同一进程内可重复调用：每次都会重建 handler，避免 Streamlit 热重载或 CLI
多次调用造成日志重复。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(log_file: str | Path | None = None, level: str = "INFO") -> logging.Logger:
    """配置根 logger；返回本平台 logger。"""

    numeric = getattr(logging, str(level).upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(numeric)

    for handler in list(root.handlers):
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:  # pragma: no cover - 关闭失败不影响主流程
            pass

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    console = logging.StreamHandler(stream=sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    if log_file is not None:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    # 第三方库日志降噪
    for noisy in ("PIL", "urllib3", "paddleocr", "paddlex", "ppocr"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logger = logging.getLogger("recruiting_capture")
    logger.debug("日志已初始化，级别=%s，文件=%s", level, log_file)
    return logger
