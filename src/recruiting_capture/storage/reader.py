"""结果读取：供 Streamlit 结果查看与 CLI 简报使用。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)


def iter_jsonl(path: str | Path) -> Iterator[dict[str, Any]]:
    target = Path(path)
    if not target.is_file():
        return
    with target.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("跳过无法解析的 JSONL 行：%s:%d", target, line_number)
                continue
            if isinstance(payload, dict):
                yield payload


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    return list(iter_jsonl(path))


def list_runs(runs_root: str | Path) -> list[Path]:
    root = Path(runs_root)
    if not root.is_dir():
        return []
    runs = [item for item in root.iterdir() if item.is_dir()]
    return sorted(runs, key=lambda item: item.name, reverse=True)


def summarize_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    if not records:
        return {
            "jobs": 0,
            "avg_desc_lines": 0.0,
            "earliest": None,
            "latest": None,
            "titles": [],
        }
    desc_lengths = [
        len(record.get("job_desc") or []) for record in records if isinstance(record, dict)
    ]
    timestamps = sorted(
        str(record.get("captured_at"))
        for record in records
        if record.get("captured_at")
    )
    return {
        "jobs": len(records),
        "avg_desc_lines": round(sum(desc_lengths) / max(1, len(desc_lengths)), 1),
        "earliest": timestamps[0] if timestamps else None,
        "latest": timestamps[-1] if timestamps else None,
        "titles": [str(record.get("job_title", "")) for record in records],
    }


def read_report(run_dir: str | Path) -> dict[str, Any] | None:
    path = Path(run_dir) / "capture_report.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("读取运行报告失败：%s（%s）", path, exc)
        return None
    return data if isinstance(data, dict) else None
