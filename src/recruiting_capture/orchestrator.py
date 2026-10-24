"""主流程编排（NEWREADME 6.4 / 6.6 / 8）。"""

from __future__ import annotations

import logging
import shutil
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .capture.window import minimize_frontend, minimize_window, restore_window
from .control import (
    DEFAULT_PAUSE_TIMEOUT_SECONDS,
    clear_decision,
    wait_for_decision,
    write_status,
)
from .engines import build_engines, build_llm, resolve_project_path
from .errors import (
    ConfigError,
    RecruitingError,
    ScreenChangedError,
    StopRequested,
)
from .modes.base import ModeContext
from .modes.short_message import ShortMessageMode
from .modes.transcribe import TranscribeMode
from .models.config import CapturePlan, SmsConfig
from .models.region import ScreenGeometry
from .storage.writers import (
    JobRecord,
    JsonlWriter,
    RunPaths,
    build_jobs_archive,
    write_json,
)
from .text_utils import generate_timestamp

logger = logging.getLogger("recruiting_capture.orchestrator")


@dataclass
class RunSummary:
    run_dir: Path
    jobs_count: int
    stop_reason: str
    stop_message: str
    records: list[JobRecord] = field(default_factory=list)
    report: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_dir": str(self.run_dir),
            "jobs_count": self.jobs_count,
            "stop_reason": self.stop_reason,
            "stop_message": self.stop_message,
            "jobs_jsonl": str(self.run_dir / "jobs.jsonl"),
            "jobs_json": str(self.run_dir / "jobs.json"),
            "report": str(self.run_dir / "capture_report.json"),
        }


def _stop_message(stop_reason: str, count: int, max_jobs: int) -> str:
    if stop_reason == "max_jobs_reached":
        return f"已达到设定的份数上限（{max_jobs} 份），正常结束。"
    if stop_reason == "list_end":
        return (
            "岗位列表已到末尾（检测到滚动停止标志），未达到设定的份数上限；"
            "列表最后几项可能未完整录入，请人工核对（NEWREADME 6.6）。"
        )
    if stop_reason == "queue_exhausted":
        return f"全部短信区域已执行完毕，共采集 {count} 份岗位。"
    if stop_reason == "error":
        return "运行过程中发生错误，已保留已采集的数据，请查看日志。"
    if stop_reason == "user_stopped":
        return "用户选择结束采集；中断前已保存的数据保留在 jobs.jsonl 中。"
    if stop_reason == "max_rounds":
        return "达到最大循环轮次，已停止；请检查滚动停止条件或页面状态。"
    return f"运行结束，共采集 {count} 份岗位。"


def run_plan(
    plan: CapturePlan,
    run_dir: str | Path,
    project_root: str | Path,
    *,
    sleep=time.sleep,
    logger_override: logging.Logger | None = None,
) -> RunSummary:
    """执行一份完整计划。

    - 每条岗位立即 append 到 ``jobs.jsonl`` 并 fsync（NFR-02）；
    - 无论成功与否都写 ``capture_report.json``；
    - 异常向上抛出，由 CLI / 前端展示可读信息。
    """

    log = logger_override or logger
    plan.validate()
    paths = RunPaths(Path(run_dir)).ensure()
    allowed_existing = {"raw", "screenshots", "run.log", "console.log"}
    existing = [item for item in paths.run_dir.iterdir()]
    if any(item.name not in allowed_existing for item in existing):
        raise ConfigError(f"运行目录非空，请换一个目录：{paths.run_dir}")

    started_at = datetime.now().astimezone()
    writer = JsonlWriter(paths.jobs_jsonl)
    records: list[JobRecord] = []
    errors: list[str] = []
    warnings: list[str] = []
    partial_saves: list[dict[str, Any]] = []
    partial_saved_counts: dict[str, int] = {}
    stop_reason = "queue_exhausted"
    stop_marker: str | None = None
    hwnd: int | None = None
    context: ModeContext | None = None
    stop_message = ""

    def _append_payload(payload: dict[str, Any], sms_name: str) -> bool:
        """把一条已采集结果写入 jobs.jsonl；到达全局上限时返回 False。"""

        if len(records) >= plan.run.max_jobs:
            return False
        record = JobRecord(
            job_title=str(payload["job_title"]),
            job_desc=list(payload["job_desc"]),
            captured_at=generate_timestamp(),
            company=payload.get("company"),
            salary=payload.get("salary"),
            sms_region=sms_name,
            source={
                "title_region": plan.output.title_region,
                "desc_region": plan.output.desc_region,
                "data_structure": (
                    plan.region_configs[sms_name].data_structure
                    if isinstance(plan.region_configs[sms_name], SmsConfig)
                    else None
                ),
            },
            warnings=list(payload.get("warnings") or []),
        )
        writer.append(record)
        records.append(record)
        log.info(
            "已落盘 %d/%d：%s（描述 %d 行）",
            len(records),
            plan.run.max_jobs,
            record.job_title,
            len(record.job_desc),
        )
        return True

    def _save_partial(
        mode: ShortMessageMode,
        sms_name: str,
        exc: BaseException,
    ) -> int:
        """等待异步去噪后，把当前区域已完成 items 增量落盘。"""

        start = partial_saved_counts.get(sms_name, 0)
        payloads = mode.recover_records(plan.output)
        saved_now = 0
        for payload in payloads[start:]:
            if not _append_payload(payload, sms_name):
                break
            saved_now += 1
        total = start + saved_now
        if saved_now:
            partial_saved_counts[sms_name] = total
            partial_saves.append(
                {
                    "region": sms_name,
                    "saved": total,
                    "new": saved_now,
                    "error": str(exc),
                }
            )
            write_json(
                paths.raw_dir / f"sms_{sms_name}.partial.json",
                {
                    "region": sms_name,
                    "saved": total,
                    "error": str(exc),
                    "records": payloads[:total],
                },
            )
            message = (
                f"短信区域 {sms_name} 中断前已保存 "
                f"{total} 条记录（原因：{exc}）"
            )
            warnings.append(message)
            log.warning(message)
        return total

    def _handle_interrupt(
        exc: RecruitingError,
        mode: ShortMessageMode,
        sms_name: str,
    ) -> str:
        """暂停、保存、等待前端选择；返回 resume/stop。"""

        saved = 0
        try:
            saved = _save_partial(mode, sms_name, exc)
        except Exception as save_exc:  # 保存失败不能覆盖原始异常
            warnings.append(f"短信区域 {sms_name} 中断前保存失败：{save_exc}")
            log.exception("短信区域 %s 中断前保存失败：%s", sms_name, save_exc)

        clear_decision(paths.run_dir)
        hwnd_value = getattr(hwnd, "hwnd", hwnd) if hwnd else None
        if hwnd_value:
            try:
                restore_window(int(hwnd_value))
            except Exception:  # pragma: no cover - 窗口恢复失败不应阻止保存
                log.exception("恢复前端窗口失败：hwnd=%s", hwnd_value)

        try:
            write_status(
                paths.run_dir,
                "paused",
                str(exc),
                saved=saved,
            )
        except Exception:  # pragma: no cover - 状态文件失败不阻塞人工处理
            log.exception("写入暂停状态失败：%s", paths.run_dir)

        log.warning(
            "区域 %s 已暂停：%s；已保存 %d 条。等待前端选择“继续采集/结束采集”。",
            sms_name,
            exc,
            saved,
        )
        try:
            action = wait_for_decision(
                paths.run_dir,
                timeout_seconds=DEFAULT_PAUSE_TIMEOUT_SECONDS,
            )
        except Exception:
            log.exception("等待继续/结束决定失败，按结束处理")
            action = "stop"

        if action == "resume":
            try:
                write_status(
                    paths.run_dir,
                    "running",
                    "用户选择继续采集",
                    saved=saved,
                )
            except Exception:  # pragma: no cover
                log.exception("写入继续状态失败：%s", paths.run_dir)
            if hwnd_value:
                try:
                    minimize_window(int(hwnd_value))
                    sleep(2.0)
                except Exception:  # pragma: no cover - 窗口最小化失败不阻止继续
                    log.exception("继续前最小化前端失败：hwnd=%s", hwnd_value)
            warnings.append(f"区域 {sms_name} 用户选择继续采集")
            log.info("区域 %s 用户选择继续采集，重试当前条目", sms_name)
            return "resume"

        reason = (
            "用户选择结束采集"
            if action == "stop"
            else "等待用户处理超时，自动结束采集"
        )
        try:
            write_status(paths.run_dir, "stopped", reason, saved=saved)
        except Exception:  # pragma: no cover
            log.exception("写入结束状态失败：%s", paths.run_dir)
        warnings.append(f"区域 {sms_name} {reason}")
        log.info("区域 %s %s；已保存 %d 条", sms_name, reason, saved)
        return "stop"

    try:
        driver, ocr, llm = build_engines(plan, project_root)

        current_size = driver.size()
        expected_size = (plan.screen.width, plan.screen.height)
        if plan.run.engine == "real" and tuple(current_size) != tuple(expected_size):
            message = (
                f"屏幕尺寸与配置不一致：配置 {expected_size[0]}x{expected_size[1]}，"
                f"当前 {current_size[0]}x{current_size[1]}。请重新截屏框选，"
                "或检查 Windows 缩放比例（R1）。"
            )
            if plan.run.on_screen_change == "abort":
                raise ScreenChangedError(message)
            warnings.append(message)
            log.warning(message)

        geometry = ScreenGeometry(
            screen_width=current_size[0],
            screen_height=current_size[1],
            shot_width=plan.screen.screenshot_width,
            shot_height=plan.screen.screenshot_height,
        )

        if plan.run.minimize_frontend and plan.run.engine == "real":
            hwnd = minimize_frontend(plan.run.window_title)
            if hwnd is None:
                warnings.append(
                    "未能定位前端窗口，未执行自动最小化（不会最小化其它窗口）；"
                    "可改用 start_delay_seconds 倒计时后手动切换窗口"
                )

        delay = max(0, int(plan.run.start_delay_seconds))
        if delay:
            log.warning(
                "将在 %d 秒后开始自动操作，请立即切换到目标页面，并不要触碰鼠标与键盘。",
                delay,
            )
            sleep(delay)

        context = ModeContext(
            driver=driver,
            ocr=ocr,
            llm=llm,
            run=plan.run,
            geometry=geometry,
            logger=log,
            sleep=sleep,
            screenshots_dir=paths.screenshots_dir,
            save_screenshots=plan.run.save_screenshots,
            # 必须是 lambda：DeepSeekClient 缺 Key 时抛 LLMError，延迟到 worker 启动时
            # （主线程、Thread.start() 之前）才调用，异常仍在主线程抛出。
            llm_factory=lambda: build_llm(plan),
        )

        transcribe_modes: dict[str, TranscribeMode] = {}
        sms_modes: dict[str, ShortMessageMode] = {}
        for name, region in plan.regions.items():
            config = plan.region_configs[name]
            if isinstance(config, SmsConfig):
                continue
            transcribe_modes[name] = TranscribeMode(region, config, context)
        for name, region in plan.regions.items():
            config = plan.region_configs[name]
            if isinstance(config, SmsConfig):
                sms_modes[name] = ShortMessageMode(
                    region, config, context, transcribe_modes
                )

        log.info(
            "计划 %s 开始执行：%d 个短信区域、%d 个抄录区域、上限 %d 份",
            plan.name,
            len(sms_modes),
            len(transcribe_modes),
            plan.run.max_jobs,
        )

        for sms_name in plan.sms_regions():
            if len(records) >= plan.run.max_jobs:
                stop_reason = "max_jobs_reached"
                break
            remaining = plan.run.max_jobs - len(records)
            sms_mode = sms_modes[sms_name]

            sms_mode.interrupt_handler = (
                lambda exc, mode=sms_mode, name=sms_name: _handle_interrupt(
                    exc, mode, name
                )
            )

            try:
                raw = sms_mode.workflow(max_items=remaining)
            except StopRequested as exc:
                stop_reason = "user_stopped"
                warnings.append(str(exc))
                log.info("用户选择结束采集：%s", exc)
                break
            except RecruitingError as exc:
                errors.append(f"短信区域 {sms_name}：{exc}")
                log.error("短信区域 %s 执行失败：%s", sms_name, exc)

                # 不是条目捕获阶段的错误（例如 analyze/click）：
                # 不进入暂停流程，但仍尽力保存当前 items。
                try:
                    _save_partial(sms_mode, sms_name, exc)
                except Exception as partial_exc:  # 保存失败不能覆盖原始异常
                    warnings.append(
                        f"短信区域 {sms_name} 中断前保存失败：{partial_exc}"
                    )
                    log.exception(
                        "短信区域 %s 中断前保存失败：%s", sms_name, partial_exc
                    )

                if plan.run.on_job_error == "skip":
                    warnings.append(f"已按 on_job_error=skip 跳过短信区域 {sms_name}")
                    continue
                raise

            write_json(paths.raw_dir / f"sms_{sms_name}.json", raw)
            warnings.extend(sms_mode.warnings)

            sms_records = sms_mode.to_records(plan.output)
            if sms_mode.stop_reason == "list_end":
                stop_reason = "list_end"
                stop_marker = sms_mode.stop_marker
            elif sms_mode.stop_reason == "max_rounds":
                stop_reason = "max_rounds"

            already_saved = partial_saved_counts.get(sms_name, 0)
            for payload in sms_records[already_saved:]:
                if not _append_payload(payload, sms_name):
                    stop_reason = "max_jobs_reached"
                    break

            log.info(
                "短信区域 %s 完成：停止原因=%s，累计落盘 %d 份",
                sms_name,
                sms_mode.stop_reason,
                len(records),
            )

            if len(records) >= plan.run.max_jobs:
                stop_reason = "max_jobs_reached"
                break
            if stop_reason in ("list_end", "max_rounds"):
                break

        if stop_reason == "queue_exhausted" and len(records) >= plan.run.max_jobs:
            stop_reason = "max_jobs_reached"
        if stop_reason == "queue_exhausted" and not records:
            stop_reason = "max_rounds"
            warnings.append("没有采集到任何岗位，请检查区域配置与页面状态")
        stop_message = _stop_message(stop_reason, len(records), plan.run.max_jobs)
        if stop_marker:
            log.info("列表停止标志：%r", stop_marker)
        log.info(stop_message)

        summary = RunSummary(
            run_dir=paths.run_dir,
            jobs_count=len(records),
            stop_reason=stop_reason,
            stop_message=stop_message,
            records=records,
        )
        return summary

    except Exception as exc:  # noqa: BLE001 - 统一记录后重抛
        errors.append(f"{type(exc).__name__}: {exc}")
        stop_reason = "error"
        stop_message = _stop_message("error", len(records), plan.run.max_jobs)
        log.error("运行失败：%s", exc)
        log.debug("异常堆栈：%s", traceback.format_exc())
        raise

    finally:
        try:
            writer.close()
        except Exception:  # pragma: no cover
            pass
        finished_at = datetime.now().astimezone()
        # 在此刻计算：所有 workflow() 已返回、worker.close() 已执行，异步计数已落定
        _sync_calls = int(getattr(llm, "calls", 0)) if "llm" in locals() else 0
        _async_calls = int((context.stats if context else {}).get("llm_calls_async", 0))
        report = {
            "schema_version": "1.0",
            "plan_name": plan.name,
            "started_at": started_at.isoformat(timespec="seconds"),
            "finished_at": finished_at.isoformat(timespec="seconds"),
            "duration_seconds": round((finished_at - started_at).total_seconds(), 2),
            "run_dir": str(paths.run_dir),
            "engine": plan.run.engine,
            "llm": plan.run.llm,
            "screen": plan.screen.to_dict(),
            "config": {
                "max_jobs": plan.run.max_jobs,
                "ocr_confidence_threshold": plan.run.ocr_confidence_threshold,
                "start_delay_seconds": plan.run.start_delay_seconds,
                "minimize_frontend": plan.run.minimize_frontend,
            },
            "counts": {
                "jobs": len(records),
                "sms_regions": len(plan.sms_regions()),
                "transcribe_regions": len(plan.transcribe_regions()),
            },
            "stop_reason": stop_reason,
            "stop_marker": stop_marker,
            "stop_message": stop_message,
            "warnings": sorted(set(warnings)),
            "errors": errors,
            "partial_saves": partial_saves,
            "stats": dict(context.stats) if context else {},
            "ocr_calls": int(getattr(ocr, "calls", 0)) if "ocr" in locals() else 0,
            "llm_calls": _sync_calls + _async_calls,
            "llm_calls_sync": _sync_calls,
            "llm_calls_async": _async_calls,
            "llm_wait_ms": int((context.stats if context else {}).get("llm_wait_ms", 0)),
            "llm_async": bool(plan.run.llm_async),
            "outputs": paths.describe(),
            "records": [record.to_dict() for record in records],
            "plan": plan.to_dict(),
        }
        try:
            write_json(paths.jobs_json, build_jobs_archive(records))
            output_copies: list[str] = []
            try:
                output_dir = resolve_project_path(project_root, plan.output.dir)
                output_dir.mkdir(parents=True, exist_ok=True)
                candidates = {
                    "jsonl": paths.jobs_jsonl,
                    "json": paths.jobs_json,
                }
                for fmt in plan.output.formats:
                    source = candidates.get(fmt)
                    if source is None or not source.is_file():
                        continue
                    destination = output_dir / f"{paths.run_dir.name}_jobs.{fmt}"
                    shutil.copyfile(source, destination)
                    output_copies.append(str(destination))
            except Exception as exc:  # pragma: no cover - 输出副本失败不影响主数据
                log.warning("写入 output.dir 副本失败：%s", exc)
            report["output_copies"] = output_copies
            write_json(paths.report, report)
        except Exception as exc:  # pragma: no cover
            log.error("写入运行报告失败：%s", exc)
        if hwnd:
            try:
                # 后台恢复（不抢焦点）；只在窗口仍处于最小化状态时才生效
                restore_window(getattr(hwnd, "hwnd", hwnd))
            except Exception:  # pragma: no cover
                pass
