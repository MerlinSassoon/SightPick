"""数据持久层：JSONL 增量写入、JSON 汇总与运行目录（7.3 / NFR-02）。"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

logger = logging.getLogger(__name__)


@dataclass
class JobRecord:
    """7.3.2 建议的一行岗位记录。"""

    job_title: str
    job_desc: list[str]
    captured_at: str
    company: str | None = None
    salary: str | None = None
    sms_region: str | None = None
    source: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    schema_version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "job_title": self.job_title,
            "job_desc": list(self.job_desc),
            "captured_at": self.captured_at,
            "company": self.company,
            "salary": self.salary,
            "sms_region": self.sms_region,
            "source": dict(self.source),
            "warnings": list(self.warnings),
        }


class JsonlWriter:
    """逐条追加并 flush+fsync，保证异常中断时已采集数据不丢失。"""

    def __init__(self, path: str | Path, *, fsync: bool = True) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fsync = fsync
        self._handle = self.path.open("a", encoding="utf-8", newline="\n")
        self.count = 0

    def append(self, record: Mapping[str, Any] | JobRecord) -> None:
        if self._handle.closed:
            raise RuntimeError(f"JSONL 写入器已关闭：{self.path}")
        payload = record.to_dict() if isinstance(record, JobRecord) else dict(record)
        line = json.dumps(payload, ensure_ascii=False)
        self._handle.write(line + "\n")
        self._handle.flush()
        if self._fsync:
            try:
                os.fsync(self._handle.fileno())
            except OSError:  # pragma: no cover - 某些文件系统不支持
                pass
        self.count += 1

    def close(self) -> None:
        if not self._handle.closed:
            self._handle.flush()
            self._handle.close()

    def __enter__(self) -> "JsonlWriter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def write_json(path: str | Path, data: Any) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return target


def build_jobs_archive(records: Iterable[JobRecord]) -> dict[str, list[str]]:
    """7.3.1 的 {岗位名称: [描述行...]} 干净版；重名岗位合并描述。"""

    archive: dict[str, list[str]] = {}
    for record in records:
        existing = archive.setdefault(record.job_title, [])
        seen = {line for line in existing}
        for line in record.job_desc:
            if line not in seen:
                existing.append(line)
                seen.add(line)
    return archive


def create_run_dir(parent: str | Path, prefix: str = "run") -> Path:
    """创建唯一运行目录 runtime/runs/<prefix>_<时间>_<随机>。"""

    parent_path = Path(parent)
    parent_path.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for _ in range(20):
        candidate = parent_path / f"{prefix}_{stamp}_{uuid.uuid4().hex[:6]}"
        try:
            candidate.mkdir(parents=True, exist_ok=False)
            return candidate
        except FileExistsError:
            continue
    raise RuntimeError(f"无法创建唯一运行目录：{parent_path}")


@dataclass(frozen=True)
class RunPaths:
    """一次运行的产物路径约定。"""

    run_dir: Path

    @property
    def jobs_jsonl(self) -> Path:
        return self.run_dir / "jobs.jsonl"

    @property
    def jobs_json(self) -> Path:
        return self.run_dir / "jobs.json"

    @property
    def report(self) -> Path:
        return self.run_dir / "capture_report.json"

    @property
    def log(self) -> Path:
        return self.run_dir / "run.log"

    @property
    def raw_dir(self) -> Path:
        return self.run_dir / "raw"

    @property
    def screenshots_dir(self) -> Path:
        return self.run_dir / "screenshots"

    def ensure(self) -> "RunPaths":
        for directory in (self.run_dir, self.raw_dir, self.screenshots_dir):
            directory.mkdir(parents=True, exist_ok=True)
        return self

    def describe(self) -> dict[str, str]:
        return {
            "run_dir": str(self.run_dir),
            "jobs_jsonl": str(self.jobs_jsonl),
            "jobs_json": str(self.jobs_json),
            "report": str(self.report),
            "log": str(self.log),
        }
