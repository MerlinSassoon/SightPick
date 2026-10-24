"""回归：批次中途失败时暂停、保存，并支持继续/结束。"""

from __future__ import annotations

import copy
import json
import threading
import time

import pytest

from recruiting_capture.control import read_status, write_decision
from recruiting_capture.errors import AnchorNotFoundError
from recruiting_capture.models.plan_io import plan_from_dict, plan_to_dict
from recruiting_capture.modes.transcribe import TranscribeMode
from recruiting_capture.orchestrator import run_plan


def _clone(plan, output_dir):
    clone = plan_from_dict(copy.deepcopy(plan_to_dict(plan)))
    clone.output.dir = str(output_dir)
    return clone


def _wait_paused(run_dir, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = read_status(run_dir)
        if status and status.get("state") == "paused":
            return status
        time.sleep(0.02)
    raise AssertionError(f"没有等到 paused 状态：{read_status(run_dir)}")


def _run_in_thread(plan, run_dir, project_root, no_sleep):
    result = {}

    def target():
        try:
            result["summary"] = run_plan(
                plan, run_dir, project_root, sleep=no_sleep
            )
        except BaseException as exc:  # noqa: BLE001 - 测试需要收集异常
            result["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread, result


def _read_lines(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_interrupt_pauses_saves_and_can_stop_or_resume(
    tmp_path, demo_plan, project_root, no_sleep, monkeypatch
) -> None:
    calls = {"C": 0}
    original = TranscribeMode.workflow

    def flaky_workflow(self, worker=None):
        if self.region_name == "C":
            calls["C"] += 1
            if calls["C"] == 3:
                raise AnchorNotFoundError("simulated anchor failure")
        return original(self, worker)

    monkeypatch.setattr(TranscribeMode, "workflow", flaky_workflow)

    # 1) 暂停后选择“结束采集”：已完成 2 条必须落盘，且不抛异常。
    plan = _clone(demo_plan, tmp_path / "output_stop")
    run_dir = tmp_path / "run_stop"
    thread, result = _run_in_thread(plan, run_dir, project_root, no_sleep)
    paused = _wait_paused(run_dir)
    assert paused["saved"] == 2
    write_decision(run_dir, "stop")
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert "error" not in result
    summary = result["summary"]
    assert summary.jobs_count == 2
    assert summary.stop_reason == "user_stopped"
    assert len(_read_lines(run_dir / "jobs.jsonl")) == 2
    report = json.loads(
        (run_dir / "capture_report.json").read_text(encoding="utf-8")
    )
    assert report["partial_saves"] == [
        {
            "region": "A",
            "saved": 2,
            "new": 2,
            "error": "simulated anchor failure",
        }
    ]
    assert read_status(run_dir)["state"] == "stopped"
    assert (run_dir / "raw" / "sms_A.partial.json").is_file()

    # 2) 暂停后选择“继续采集”：重试当前条目，最终 3 条，不重复落盘前 2 条。
    calls["C"] = 0
    plan = _clone(demo_plan, tmp_path / "output_resume")
    run_dir = tmp_path / "run_resume"
    thread, result = _run_in_thread(plan, run_dir, project_root, no_sleep)
    _wait_paused(run_dir)
    write_decision(run_dir, "resume")
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert "error" not in result
    summary = result["summary"]
    assert summary.jobs_count == 3
    records = _read_lines(run_dir / "jobs.jsonl")
    assert len(records) == 3
    assert len({record["job_title"] for record in records}) == 3
    report = json.loads(
        (run_dir / "capture_report.json").read_text(encoding="utf-8")
    )
    assert report["stop_reason"] in {"list_end", "max_jobs_reached"}
    assert report["partial_saves"][0]["saved"] == 2
    assert len(_read_lines(run_dir / "jobs.jsonl")) == 3
