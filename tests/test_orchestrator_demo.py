"""端到端：mock 引擎跑完整编排，验证增量落盘、去重、去噪与终止原因。"""

from __future__ import annotations

import copy
import json

import pytest

from recruiting_capture.errors import ConfigError, LLMError
from recruiting_capture.models.plan_io import plan_from_dict, plan_to_dict
from recruiting_capture.orchestrator import run_plan
from recruiting_capture.text_utils import is_noise_line

NOISE = {"魏女士", "刚刚活跃", "去App", "与BOSS随时沟通", "职位描述"}


def _clone(plan, output_dir=None):
    clone = plan_from_dict(copy.deepcopy(plan_to_dict(plan)))
    if output_dir is not None:
        clone.output.dir = str(output_dir)
    return clone


def test_demo_run_end_to_end(tmp_path, demo_plan, project_root, no_sleep) -> None:
    run_dir = tmp_path / "run"
    plan = _clone(demo_plan, tmp_path / "output")
    summary = run_plan(plan, run_dir, project_root, sleep=no_sleep)

    assert summary.jobs_count == 3
    assert summary.stop_reason == "list_end"

    lines = [
        json.loads(line)
        for line in (run_dir / "jobs.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(lines) == 3
    assert [record["job_title"] for record in lines] == [
        "中高级开发工程师（20-40k*14-16薪）",
        "数据开发工程师（自研&双休）",
        "python开发工程师",
    ]
    for record in lines:
        desc = record["job_desc"]
        assert desc
        assert len(desc) == len(set(desc)), "描述出现重复行"
        assert not (set(desc) & NOISE), "描述仍含界面噪声"
        assert not any("工作地址" in line for line in desc)
        assert record["captured_at"]
        assert record["sms_region"] == "A"

    archive = json.loads((run_dir / "jobs.json").read_text(encoding="utf-8"))
    assert set(archive) == {record["job_title"] for record in lines}

    report = json.loads((run_dir / "capture_report.json").read_text(encoding="utf-8"))
    assert report["stop_reason"] == "list_end"
    assert report["counts"]["jobs"] == 3
    assert report["errors"] == []
    assert (run_dir / "raw" / "sms_A.json").is_file()
    assert report["ocr_calls"] > 0
    assert report["llm_calls"] > 0


def test_max_jobs_stop(tmp_path, demo_plan, project_root, no_sleep) -> None:
    plan = _clone(demo_plan, tmp_path / "output")
    plan.run.max_jobs = 2
    run_dir = tmp_path / "run"
    summary = run_plan(plan, run_dir, project_root, sleep=no_sleep)
    assert summary.jobs_count == 2
    assert summary.stop_reason == "max_jobs_reached"
    assert "份数上限" in summary.stop_message
    lines = (run_dir / "jobs.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


def test_missing_deepseek_key_writes_error_report(
    tmp_path, demo_plan, project_root, monkeypatch, no_sleep
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    plan = _clone(demo_plan, tmp_path / "output")
    plan.run.llm = "deepseek"
    run_dir = tmp_path / "run"
    with pytest.raises(LLMError):
        run_plan(plan, run_dir, project_root, sleep=no_sleep)
    report = json.loads((run_dir / "capture_report.json").read_text(encoding="utf-8"))
    assert report["stop_reason"] == "error"
    assert report["errors"]
    assert (run_dir / "jobs.jsonl").is_file()


def test_reusing_run_dir_is_rejected(tmp_path, demo_plan, project_root, no_sleep) -> None:
    plan = _clone(demo_plan, tmp_path / "output")
    run_dir = tmp_path / "run"
    run_plan(plan, run_dir, project_root, sleep=no_sleep)
    with pytest.raises(ConfigError):
        run_plan(plan, run_dir, project_root, sleep=no_sleep)


def test_all_desc_lines_clean(tmp_path, demo_plan, project_root, no_sleep) -> None:
    plan = _clone(demo_plan, tmp_path / "output")
    run_dir = tmp_path / "run"
    summary = run_plan(plan, run_dir, project_root, sleep=no_sleep)
    for record in summary.records:
        assert all(not is_noise_line(line) for line in record.job_desc)


def test_async_matches_sync_byte_for_byte(tmp_path, demo_plan, project_root, no_sleep) -> None:
    """I-3：llm_async=false/true 两次运行结果必须逐字节相同。

    唯一的规范化是剔除 captured_at：两次运行由 generate_timestamp() 生成不同的墙钟时间，
    与异步执行无关；其余字段（含行序、描述内容、告警）一律严格比较。
    """

    def _normalized(path) -> list[str]:
        lines = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            record = json.loads(raw)
            record.pop("captured_at", None)
            lines.append(json.dumps(record, ensure_ascii=False, sort_keys=True))
        return lines

    runs = {}
    reports = {}
    for flag in (False, True):
        plan = _clone(demo_plan, tmp_path / f"output_{flag}")
        plan.run.llm_async = flag
        run_dir = tmp_path / f"run_{flag}"
        run_plan(plan, run_dir, project_root, sleep=no_sleep)
        runs[flag] = _normalized(run_dir / "jobs.jsonl")
        reports[flag] = json.loads(
            (run_dir / "capture_report.json").read_text(encoding="utf-8")
        )
        assert (run_dir / "raw" / "sms_A.json").is_file()

    assert runs[False] == runs[True]
    assert len(runs[False]) == 3
    assert reports[False]["llm_async"] is False
    assert reports[True]["llm_async"] is True
    for report in reports.values():
        assert report["llm_calls"] > 0
        assert report["llm_calls"] == report["llm_calls_sync"] + report["llm_calls_async"]
        assert report["llm_wait_ms"] >= 0
