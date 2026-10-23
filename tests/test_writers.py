"""T8：JSONL 增量落盘与中断恢复。"""

from __future__ import annotations

import json

import pytest

from recruiting_capture.storage.reader import read_jsonl, summarize_records
from recruiting_capture.storage.writers import (
    JobRecord,
    JsonlWriter,
    build_jobs_archive,
    create_run_dir,
    write_json,
)


def _record(index: int) -> JobRecord:
    return JobRecord(
        job_title=f"岗位{index}",
        job_desc=[f"描述{index}-1", f"描述{index}-2"],
        captured_at="2026-10-22T21:00:00+08:00",
        company="某公司",
        salary="10-20K",
        sms_region="A",
        source={"title_region": "C", "desc_region": "B"},
    )


def test_jsonl_append_and_read(tmp_path) -> None:
    path = tmp_path / "jobs.jsonl"
    with JsonlWriter(path, fsync=False) as writer:
        writer.append(_record(1))
        writer.append(_record(2))
    records = read_jsonl(path)
    assert [item["job_title"] for item in records] == ["岗位1", "岗位2"]
    assert records[0]["job_desc"] == ["描述1-1", "描述1-2"]
    assert records[0]["schema_version"] == "1.0"
    summary = summarize_records(records)
    assert summary["jobs"] == 2
    assert summary["avg_desc_lines"] == 2.0


def test_jsonl_survives_interruption(tmp_path) -> None:
    path = tmp_path / "jobs.jsonl"
    writer = JsonlWriter(path, fsync=False)
    try:
        writer.append(_record(1))
        writer.append(_record(2))
        raise RuntimeError("模拟程序崩溃")
    except RuntimeError:
        pass
    finally:
        writer.close()
    records = read_jsonl(path)
    assert len(records) == 2
    assert records[-1]["job_title"] == "岗位2"


def test_archive_merges_duplicate_titles() -> None:
    first = _record(1)
    second = JobRecord(
        job_title="岗位1",
        job_desc=["描述1-2", "新增行"],
        captured_at="2026-10-22T21:00:01+08:00",
    )
    archive = build_jobs_archive([first, second])
    assert archive["岗位1"] == ["描述1-1", "描述1-2", "新增行"]


def test_write_json_unicode(tmp_path) -> None:
    path = write_json(tmp_path / "a.json", {"岗位": "中文"})
    assert "中文" in path.read_text(encoding="utf-8")


def test_create_run_dir_unique(tmp_path) -> None:
    first = create_run_dir(tmp_path, prefix="run")
    second = create_run_dir(tmp_path, prefix="run")
    assert first != second
    assert first.is_dir() and second.is_dir()


def test_record_requires_valid_desc_list_type() -> None:
    record = _record(1)
    payload = record.to_dict()
    json.dumps(payload, ensure_ascii=False)
    assert isinstance(payload["job_desc"], list)
