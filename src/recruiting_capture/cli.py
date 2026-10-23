"""命令行入口：demo / run / validate / scroll-test / capture-screen。"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any, Sequence

from .errors import RecruitingError
from .logging_setup import setup_logging
from .models.plan_io import load_plan
from .orchestrator import RunSummary, run_plan
from .storage.writers import create_run_dir

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _resolve(path_value: str) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    if path.exists():
        return path.resolve()
    return (PROJECT_ROOT / path).resolve()


def _apply_overrides(plan, args: argparse.Namespace) -> None:
    if getattr(args, "engine", None):
        plan.run.engine = args.engine
    if getattr(args, "llm", None):
        plan.run.llm = args.llm
    if getattr(args, "max_jobs", None) is not None:
        plan.run.max_jobs = int(args.max_jobs)
    if getattr(args, "output_dir", None):
        plan.output.dir = args.output_dir
    if getattr(args, "minimize", None) is not None:
        plan.run.minimize_frontend = bool(args.minimize)
    if getattr(args, "window_title", None):
        plan.run.window_title = args.window_title
    if getattr(args, "start_delay", None) is not None:
        plan.run.start_delay_seconds = int(args.start_delay)
    plan.validate()


def _print_plan(plan) -> None:
    print(f"计划：{plan.name}（schema {plan.schema_version}）")
    print(
        f"屏幕：{plan.screen.width}x{plan.screen.height}"
        f"；截图：{plan.screen.screenshot_width}x{plan.screen.screenshot_height}"
    )
    print(f"上限：{plan.run.max_jobs} 份；引擎：{plan.run.engine}；大模型：{plan.run.llm}")
    for name, region in plan.regions.items():
        config = plan.region_configs[name]
        mode = "短信模式" if config.mode == "sms" else "抄录模式"
        print(f"  {name}: {region.as_list()}  {mode}")
    if plan.output.title_region or plan.output.desc_region:
        print(
            f"输出映射：标题={plan.output.title_region or '未设置'}，"
            f"描述={plan.output.desc_region or '未设置'}"
        )


def _print_summary(summary: RunSummary) -> None:
    print("")
    print("=" * 68)
    print(f"运行结果：采集 {summary.jobs_count} 份，停止原因：{summary.stop_reason}")
    print(summary.stop_message)
    print(f"运行目录：{summary.run_dir}")
    print(f"  - {summary.run_dir / 'jobs.jsonl'}")
    print(f"  - {summary.run_dir / 'jobs.json'}")
    print(f"  - {summary.run_dir / 'capture_report.json'}")
    print(f"  - {summary.run_dir / 'run.log'}")
    if summary.records:
        print("已采集岗位：")
        for index, record in enumerate(summary.records, start=1):
            print(f"  {index}. {record.job_title}（描述 {len(record.job_desc)} 行）")
    print("=" * 68)


# ---------------------------------------------------------------------- #
# 子命令
# ---------------------------------------------------------------------- #
def cmd_validate(args: argparse.Namespace) -> int:
    plan = load_plan(_resolve(args.plan))
    _print_plan(plan)
    print("计划校验通过。")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    plan = load_plan(_resolve(args.plan))
    _apply_overrides(plan, args)
    run_dir = (
        _resolve(args.run_dir)
        if args.run_dir
        else create_run_dir(PROJECT_ROOT / "runtime" / "runs", prefix="run")
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    log = setup_logging(run_dir / "run.log", level=args.log_level)
    log.info("命令行启动：plan=%s run_dir=%s", args.plan, run_dir)
    _print_plan(plan)
    print(f"运行目录：{run_dir}")
    try:
        summary = run_plan(plan, run_dir, PROJECT_ROOT)
    except Exception as exc:  # noqa: BLE001 - CLI 统一给出可读错误
        log.exception("运行失败")
        print(f"\n[失败] {type(exc).__name__}: {exc}", file=sys.stderr)
        print(f"日志：{run_dir / 'run.log'}", file=sys.stderr)
        return 1
    _print_summary(summary)
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    if args.engine == "real":
        print(
            "demo 子命令只用于 mock 演示，不会操作真实鼠标；"
            "真实采集请使用 `python run.py run --plan ...`。",
            file=sys.stderr,
        )
        return 2
    plan = load_plan(PROJECT_ROOT / "configs" / "demo_plan.json")
    if args.llm:
        plan.run.llm = args.llm
    if args.max_jobs is not None:
        plan.run.max_jobs = int(args.max_jobs)
    plan.validate()
    run_dir = (
        _resolve(args.run_dir)
        if args.run_dir
        else create_run_dir(PROJECT_ROOT / "runtime" / "runs", prefix="demo")
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    log = setup_logging(run_dir / "run.log", level=args.log_level)
    log.info("演示模式启动：%s", run_dir)
    print("演示模式：mock 屏幕 + mock OCR + mock 大模型，不会移动你的鼠标。")
    try:
        summary = run_plan(plan, run_dir, PROJECT_ROOT)
    except Exception as exc:  # noqa: BLE001
        log.exception("演示运行失败")
        print(f"\n[失败] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    _print_summary(summary)
    print("你可以直接查看 jobs.jsonl / capture_report.json 验证结果。")
    return 0


def cmd_scroll_test(args: argparse.Namespace) -> int:
    try:
        import pyautogui
    except Exception as exc:  # pragma: no cover
        print(f"[失败] 无法导入 pyautogui：{exc}", file=sys.stderr)
        return 1
    pyautogui.FAILSAFE = True
    print(f"滚动测试将在 {args.delay} 秒后开始，请立即切换到目标页面。")
    print("注意：运行期间不要触碰鼠标键盘；把鼠标移到屏幕左上角可急停。")
    time.sleep(max(0.0, float(args.delay)))
    pyautogui.moveTo(int(args.x), int(args.y), duration=0.2)
    for index in range(1, int(args.times) + 1):
        time.sleep(max(0.0, float(args.interval)))
        pyautogui.scroll(int(args.distance))
        print(f"第 {index}/{args.times} 次滚动：{args.distance}")
    print("滚动测试结束。请观察列表首尾是否重叠、有无缺漏。")
    return 0


def cmd_capture_screen(args: argparse.Namespace) -> int:
    from .capture.pyautogui_driver import PyAutoGuiDriver
    from .capture.window import minimize_frontend, restore_window

    output = _resolve(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    driver = PyAutoGuiDriver()
    width, height = driver.size()

    delay = max(0, int(args.delay))
    if delay:
        print(f"倒计时 {delay} 秒后截图，请切换到目标页面（期间不操作任何窗口）。")
        time.sleep(delay)

    target = None
    if args.minimize:
        keyword = (args.window_title or "").strip()
        if not keyword:
            print(
                "未提供 --window-title：为避免误最小化命令行等其它窗口，本次不做最小化；"
                "请填写窗口标题关键字，或改用 --no-minimize 配合倒计时。",
                file=sys.stderr,
            )
        else:
            target = minimize_frontend(keyword)
            if target is None:
                print(
                    f"提示：未找到标题包含“{keyword}”的前端窗口，已跳过自动最小化"
                    "（不会最小化其它窗口）；可改用 --no-minimize 配合倒计时。",
                    file=sys.stderr,
                )
            else:
                time.sleep(0.8)
    try:
        frame = driver.capture((0, 0, width, height), label="fullscreen")
    finally:
        if target is not None:
            restore_window(target.hwnd)

    from PIL import Image

    Image.fromarray(frame.image).save(output)
    print(f"已截取首屏：{output}（{width}x{height}）")
    return 0


# ---------------------------------------------------------------------- #
# 参数解析
# ---------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="recruiting-capture",
        description="智能招聘数据抓取与可视化平台 · 命令行入口",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="校验采集计划 JSON")
    validate.add_argument("--plan", required=True, help="计划文件路径")
    validate.set_defaults(func=cmd_validate)

    run = sub.add_parser("run", help="按计划执行采集")
    run.add_argument("--plan", required=True, help="计划文件路径")
    run.add_argument("--run-dir", default=None, help="指定运行目录（默认自动创建）")
    run.add_argument("--engine", choices=["real", "mock"], default=None)
    run.add_argument("--llm", choices=["deepseek", "mock"], default=None)
    run.add_argument("--max-jobs", type=int, default=None)
    run.add_argument("--output-dir", default=None)
    run.add_argument(
        "--minimize",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="是否自动最小化前端窗口",
    )
    run.add_argument("--window-title", default=None, help="前端窗口标题关键字")
    run.add_argument("--start-delay", type=int, default=None, help="开始前倒计时秒数")
    run.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"]
    )
    run.set_defaults(func=cmd_run)

    demo = sub.add_parser("demo", help="使用 mock 引擎运行内置演示（不移动鼠标）")
    demo.add_argument("--run-dir", default=None)
    demo.add_argument("--engine", choices=["real", "mock"], default=None)
    demo.add_argument("--llm", choices=["deepseek", "mock"], default=None)
    demo.add_argument("--max-jobs", type=int, default=None)
    demo.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"]
    )
    demo.set_defaults(func=cmd_demo)

    scroll = sub.add_parser("scroll-test", help="滚动距离实时测试（NEWREADME FR-04）")
    scroll.add_argument("--x", type=int, required=True)
    scroll.add_argument("--y", type=int, required=True)
    scroll.add_argument("--distance", type=int, required=True, help="负值向下滚动")
    scroll.add_argument("--times", type=int, default=5)
    scroll.add_argument("--interval", type=float, default=0.5)
    scroll.add_argument("--delay", type=float, default=3.0)
    scroll.set_defaults(func=cmd_scroll_test)

    capture = sub.add_parser("capture-screen", help="截取首屏（可自动最小化前端）")
    capture.add_argument("--out", default="runtime/ui/first_screen.png")
    capture.add_argument(
        "--minimize", action=argparse.BooleanOptionalAction, default=True
    )
    capture.add_argument(
        "--window-title",
        default=None,
        help="前端窗口标题关键字（自动最小化模式必填；留空则不做最小化）",
    )
    capture.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help="倒计时秒数：大于 0 时先等待再截图（与前端“倒计时模式”对应）",
    )
    capture.set_defaults(func=cmd_capture_screen)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except RecruitingError as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n[已中断] 用户取消了运行；已落盘的数据不会丢失。", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001
        print(f"[错误] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
