"""Streamlit 前端：截图框选、区域配置、滚动测试、运行与结果查看。

运行：
    cd VibeCoding_Project
    streamlit run app/streamlit_app.py

说明：
- 所有区域坐标都是“**原始截图**像素”坐标，写入区域字典前按显示缩放比例还原；
- 只在服务端本机执行 pyautogui，因此必须在本机浏览器中打开；
- 组件 ``click_and_drag`` 用运行时签名探测，旧版本自动降级为两次点击框选；
- 截首屏支持两种窗口处理方式：自动最小化前端窗口 / 倒计时模式（手动切换窗口）。
"""

from __future__ import annotations

import hashlib
import inspect
import io
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import streamlit as st
from PIL import Image
from streamlit_image_coordinates import streamlit_image_coordinates
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from recruiting_capture.capture.pyautogui_driver import PyAutoGuiDriver  # noqa: E402
from recruiting_capture.capture.window import (  # noqa: E402
    WindowAction,
    find_frontend_window,
    minimize_window,
    restore_window,
)
from recruiting_capture.display import (  # noqa: E402
    DisplayMapping,
    compute_display_size,
    describe_mapping,
    encode_display_bytes,
    mapping_from_dict,
    mapping_to_dict,
)
from recruiting_capture.errors import ConfigError  # noqa: E402
from recruiting_capture.imaging import render_regions  # noqa: E402
from recruiting_capture.models.config import MODE_SMS, MODE_TRANSCRIBE  # noqa: E402
from recruiting_capture.models.plan_io import load_plan, plan_to_dict, save_plan  # noqa: E402
from recruiting_capture.models.region import Region, ScreenGeometry  # noqa: E402
from recruiting_capture.plan_builder import PlanBuilder  # noqa: E402
from recruiting_capture.storage.reader import (  # noqa: E402
    list_runs,
    read_jsonl,
    read_report,
    summarize_records,
)
from recruiting_capture.storage.writers import create_run_dir  # noqa: E402

RUNS_DIR = ROOT / "runtime" / "runs"
PLANS_DIR = ROOT / "runtime" / "plans"
UI_DIR = ROOT / "runtime" / "ui"
SAMPLES_DIR = ROOT / "data" / "samples"
SAMPLE_SCREENSHOT = SAMPLES_DIR / "screenshot_1080p.png"
EXAMPLE_PLAN = ROOT / "configs" / "boss_1080p_example.json"

ORIGINAL_SCREENSHOT = UI_DIR / "first_screen.png"
DISPLAY_SCREENSHOT = UI_DIR / "first_screen_display.jpg"

WIN_MODE_MINIMIZE = "自动最小化前端窗口"
WIN_MODE_COUNTDOWN = "倒计时模式（手动切换窗口）"
WIN_MODE_CHOICES = (WIN_MODE_MINIMIZE, WIN_MODE_COUNTDOWN)
DEFAULT_COUNTDOWN_SECONDS = 5
STABILIZE_SECONDS = 3.0  # 最小化/切换窗口后固定等待页面稳定的原有行为

# 前端窗口定位参数：两者语义严格区分（修复“关键字被当作 Streamlit 节点标题”的问题）
# - 窗口标题关键字：用户输入，作为主匹配条件，必须出现在窗口标题中；
# - Streamlit 节点标题：固定契约，只有“标题命中用户关键字”的窗口才会再按它做精细选择。
DEFAULT_WINDOW_TITLE_KEYWORD = ""  # 不做默认预填，避免占位值掩盖真实窗口标题
STREAMLIT_NODE_TITLE = "Streamlit"

# 两处“前端窗口标题关键字”输入控件共用同一个会话键，保证取值一致
WINDOW_KEYWORD_KEY = "window_title_keyword"

DISPLAY_MAX_WIDTH = 1280
DISPLAY_MAX_HEIGHT = 800
DISPLAY_QUALITY_CHOICES = {
    "高（JPEG 92，原始画质）": 92,
    "中（JPEG 85，推荐）": 85,
    "低（JPEG 70，加载更快）": 70,
}

st.set_page_config(page_title="智能招聘数据抓取平台", layout="wide")


# ---------------------------------------------------------------------- #
# 工具函数
# ---------------------------------------------------------------------- #
def _init_state() -> None:
    defaults = {
        "builder": None,
        "screenshot_path": None,
        "display_mapping": None,
        "display_max_width": DISPLAY_MAX_WIDTH,
        "display_max_height": DISPLAY_MAX_HEIGHT,
        "display_quality": DISPLAY_QUALITY_CHOICES["中（JPEG 85，推荐）"],
        "window_action": None,
        "window_probe": None,
        "window_diagnostic": None,
        WINDOW_KEYWORD_KEY: DEFAULT_WINDOW_TITLE_KEYWORD,
        "countdown": {
            "active": False,
            "mode": "countdown",
            "seconds": DEFAULT_COUNTDOWN_SECONDS,
            "remaining": 0,
            "target": None,
            "page_keyword": None,
            "stabilize": STABILIZE_SECONDS,
        },
        "last_pick_signature": None,
        "picker_nonce": 0,
        "pending_point": None,
        "measure_start": None,
        "measure_mode": False,
        "run_proc": None,
        "run_dir": None,
        "plan_path": None,
        "scroll_proc": None,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def _tail(path: Path | str | None, lines: int = 80) -> str:
    if not path:
        return ""
    target = Path(path)
    if not target.is_file():
        return ""
    try:
        content = target.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(content[-lines:])


def _count_jsonl(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return sum(1 for line in handle if line.strip())
    except OSError:
        return 0


def _parse_point(raw: str) -> list[int] | None:
    try:
        parts = [int(item.strip()) for item in raw.replace("，", ",").split(",") if item.strip()]
    except ValueError:
        return None
    return parts if len(parts) == 2 else None


def _parse_markers(raw: str) -> list[str]:
    normalized = (
        raw.replace("，", ",")
        .replace("、", ",")
        .replace("；", ",")
        .replace(";", ",")
        .replace("\n", ",")
    )
    return [item.strip() for item in normalized.split(",") if item.strip()]


def _point_text(value: list[int] | None) -> str:
    return f"{value[0]}, {value[1]}" if value else ""


def _number_value(returned: object, key: str, default: int) -> int:
    """取数字控件值：缺失时回退到“控件 key 对应的会话值/默认值”，避免 None 崩溃。"""

    if isinstance(returned, (int, float)) and not isinstance(returned, bool):
        return int(returned)
    stored = st.session_state.get(key)
    if isinstance(stored, (int, float)) and not isinstance(stored, bool):
        return int(stored)
    return int(default)


def _select_value(returned: object, options: list[str], key: str, default_index: int) -> str:
    if isinstance(returned, str) and returned in options:
        return returned
    stored = st.session_state.get(key)
    if isinstance(stored, str) and stored in options:
        return stored
    return options[default_index]


def _clock_value(stored: object, key: str, default: int) -> int:
    """number_input 的 ``value=``：沿用会话里已存的值，缺失时回退默认值且不越界。"""

    if isinstance(stored, (int, float)) and not isinstance(stored, bool):
        return max(1, min(60, int(stored)))
    return int(default)


def _quality_label(quality: int) -> str:
    """质量数值 → 下拉框选项文案（找不到时回退到“中”）。"""

    for label, value in DISPLAY_QUALITY_CHOICES.items():
        if value == quality:
            return label
    return list(DISPLAY_QUALITY_CHOICES)[1]


# ---------------------------------------------------------------------- #
# 截屏与展示：原图（坐标基准） + 缩放副本（画布展示）
# ---------------------------------------------------------------------- #
@dataclass(frozen=True)
class CaptureResult:
    """一次截屏后的状态。"""

    width: int
    height: int
    target: Path | None
    mapping: DisplayMapping
    display_path: Path


def _display_size_for(mapping: DisplayMapping, max_width: int, max_height: int) -> tuple[int, int, DisplayMapping]:
    """在保持宽高比的前提下，按当前 max_width/max_height 重新计算展示尺寸。"""

    return compute_display_size(
        mapping.original_width, mapping.original_height, max_width, max_height
    )


def _write_display_copy(
    image: Image.Image,
    dest: Path,
    mapping: DisplayMapping,
    quality: int,
) -> bytes:
    """把（已缩放的）图片写入磁盘并返回展示字节流。"""

    force_jpeg = not mapping.is_identity and mapping.review_ratio < 0.75
    payload, mime = encode_display_bytes(image, jpeg_quality=quality, force_jpeg=force_jpeg)
    final = dest.with_suffix(".jpg" if mime == "image/jpeg" else ".png")
    final.parent.mkdir(parents=True, exist_ok=True)
    final.write_bytes(payload)
    return payload


def _clean_title_keyword(raw) -> str:
    """清洗用户输入的窗口标题关键字：去掉首尾与折叠内部空白；纯空白视为未填写。"""

    if raw is None:
        return ""
    text = str(raw).replace("\u3000", " ").strip()
    return " ".join(text.split())


def _render_window_keyword_input(container, key: str) -> str:
    """渲染“前端窗口标题关键字”输入框。

    两处入口（截图页、运行页）写入同一个会话键 ``WINDOW_KEYWORD_KEY``，
    另一处通过默认值读取，因此一处修改、另一处同步；返回清洗后的关键字。
    """

    current = _clean_title_keyword(st.session_state.get(WINDOW_KEYWORD_KEY, ""))
    value = container.text_input(
        "前端窗口标题关键字",
        key=key,
        value=current,
        placeholder="例如：智能招聘数据抓取平台（留空则列出当前窗口供选择）",
        help=(
            "要最小化的前端窗口标题里包含的文字（不区分大小写），"
            "例如浏览器里的“智能招聘数据抓取平台”或“Streamlit”。"
        ),
    )
    cleaned = _clean_title_keyword(value)
    st.session_state[WINDOW_KEYWORD_KEY] = cleaned
    return cleaned


def _window_keyword_file() -> Path:
    """窗口关键字与选择结果的保存路径（不写入计划/配置）。"""

    return UI_DIR / "window_keyword.txt"


def _save_window_keyword(keyword: str) -> None:
    try:
        UI_DIR.mkdir(parents=True, exist_ok=True)
        _window_keyword_file().write_text(keyword, encoding="utf-8")
    except OSError:
        pass


def _load_window_keyword() -> str:
    path = _window_keyword_file()
    if not path.is_file():
        return ""
    try:
        return _clean_title_keyword(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return ""


def _list_visible_windows() -> list:
    """枚举当前可见窗口（只读）；失败时返回空列表并把原因写入诊断。"""

    try:
        from recruiting_capture.capture import host as host_module

        return host_module.list_windows()
    except Exception as exc:  # pragma: no cover - 依赖系统 API
        st.session_state["window_diagnostic"] = f"枚举窗口失败：{exc}"
        return []


def _matching_windows(keyword: str, windows: list | None = None) -> list:
    """按窗口标题关键字过滤窗口（不区分大小写；空关键字返回空列表）。"""

    needle = _clean_title_keyword(keyword).lower()
    if not needle:
        return []
    pool = list(windows) if windows is not None else _list_visible_windows()
    return [window for window in pool if needle in (window.title or "").lower()]


def _render_window_diagnostic(keyword: str) -> None:
    """在界面上展示最近一次定位诊断与当前窗口列表（替代原控制台打印）。"""

    diagnostic = st.session_state.get("window_diagnostic")
    if diagnostic:
        st.caption(f"最近一次窗口匹配：{diagnostic}")
    with st.expander("查看匹配到的窗口 / 当前全部窗口", expanded=False):
        if keyword:
            matched = _matching_windows(keyword)
            if matched:
                st.write(f"标题包含“{keyword}”的窗口：")
                for window in matched:
                    st.write(f"- {window.title}（hwnd={window.hwnd}）")
            else:
                st.warning(f"当前没有标题包含“{keyword}”的可见窗口。")
        all_windows = _list_visible_windows()
        if all_windows:
            st.write("当前全部可见窗口：")
            for window in all_windows:
                suffix = "（已最小化）" if getattr(window, "is_minimized", False) else ""
                st.write(f"- {window.title}（hwnd={window.hwnd}）{suffix}")
        else:
            st.caption("未枚举到可见窗口（或当前不是 Windows 桌面会话）。")


def _remember_window_action(action: WindowAction, keyword: str) -> None:
    """把窗口操作结果（含失败原因）写入会话，供截图前显示。"""

    st.session_state["window_action"] = {
        "ok": action.ok,
        "message": action.describe(),
        "reason": action.reason,
        "minimized": action.minimized,
        "restored": action.restored,
        "target": action.target.title if action.target else None,
        "hwnd": action.target.hwnd if action.target else None,
        "keyword": keyword,
    }


def _current_route_keyword() -> str | None:
    """读取本页面 URL 中的 ``?page=`` 路由参数（由外层启动器注入），用于在标题命中后精确路由。"""

    try:
        value = st.query_params.get("page")
    except Exception:  # pragma: no cover - 兼容旧版 Streamlit
        try:
            params = st.experimental_get_query_params()
            value = (params.get("page") or [None])[0]
        except Exception:
            value = None
    if isinstance(value, list):
        value = value[0] if value else None
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _apply_window_action(
    mode: str,
    window_title_keyword: str,
    node_title: str,
    page_keyword: str | None,
) -> WindowAction:
    """按所选方式处理窗口。

    - 最小化模式：只最小化**定位到的前端窗口**，定位失败就跳过（绝不碰其它窗口）；
    - 倒计时模式：不做任何窗口操作。

    参数语义严格区分：
    - ``window_title_keyword``：用户输入的窗口标题关键字，是**主匹配条件**（先清洗）；
    - ``node_title``：Streamlit 节点标题，只在“标题已命中关键字”的窗口里做精细选择。

    无论成功与否，都会把实际使用的关键字与真实失败原因写入 ``window_action``；
    本函数不做任何控制台调试打印，也不依赖 pygetwindow。
    """

    keyword = _clean_title_keyword(window_title_keyword)
    page = _clean_title_keyword(page_keyword) or None

    if mode == WIN_MODE_COUNTDOWN:
        return WindowAction(reason="倒计时模式：未对任何窗口执行最小化")

    if mode != WIN_MODE_MINIMIZE:
        return WindowAction(reason=f"未知的窗口处理方式：{mode}")

    if not keyword:
        reason = (
            "未填写前端窗口标题关键字：为避免误最小化命令行等其它窗口，本次不做最小化。"
            "请在下方“前端窗口标题关键字”中填写目标窗口标题的一部分，或改用“倒计时模式”。"
        )
        st.session_state["window_diagnostic"] = reason
        return WindowAction(reason=reason)

    # 先回显“实际参与匹配的关键字”，即使用户输入带多余空格也能看到真实值
    st.caption(f"本次按窗口标题包含“{keyword}”匹配前端窗口（不区分大小写）。")

    try:
        target = find_frontend_window(keyword, page_keyword=page, node_title=node_title)
    except TypeError:
        # 兼容未接受 node_title 的旧版窗口模块
        try:
            target = find_frontend_window(keyword, page_keyword=page)
        except Exception as exc:  # pragma: no cover - 依赖系统 API
            reason = f"窗口定位异常：{type(exc).__name__}: {exc}"
            st.session_state["window_diagnostic"] = reason
            return WindowAction(reason=reason)
    except Exception as exc:  # pragma: no cover - 依赖系统 API
        reason = f"窗口定位异常：{type(exc).__name__}: {exc}"
        st.session_state["window_diagnostic"] = reason
        return WindowAction(reason=reason)

    if target is None:
        reason = (
            f"未找到标题包含“{keyword}”的前端窗口，本次不做最小化"
            "（为避免误最小化命令行等其它窗口）。"
            "请确认窗口标题文字后重试，或改用“倒计时模式”手动切换到目标页面。"
        )
        matched = _matching_windows(keyword)
        diagnostic = (
            f"关键字“{keyword}”：找到 {len(matched)} 个标题匹配的窗口，但没有可用的前端窗口"
            if matched
            else f"关键字“{keyword}”：当前没有标题包含该关键字的可见窗口"
        )
        st.session_state["window_diagnostic"] = diagnostic
        return WindowAction(reason=reason)

    minimized = minimize_window(target.hwnd)
    if not minimized:
        reason = f"已定位窗口 {target.describe()}，但调用系统最小化失败（窗口可能已关闭或权限不足）"
        st.session_state["window_diagnostic"] = reason
        return WindowAction(target=target, reason=reason)

    st.session_state["window_diagnostic"] = f"关键字“{keyword}”命中并最小化了 {target.describe()}"
    return WindowAction(target=target, minimized=True)


def _capture_fullscreen(
    mode: str,
    window_title_keyword: str,
    node_title: str,
    page_keyword: str | None,
    *,
    maximize_hint: bool = False,
) -> tuple[object, WindowAction]:
    """截取全屏：窗口处理 → 固定稳定等待 → 截图；窗口在截图后后台恢复。

    窗口操作的真实结果（含失败原因）写进 ``window_action`` 并在截图前显示，
    避免“没最小化却照常截到前端、界面无任何提示”的静默失败。
    """

    keyword = _clean_title_keyword(window_title_keyword)
    driver = PyAutoGuiDriver()
    action = _apply_window_action(mode, keyword, node_title, page_keyword)
    _remember_window_action(action, keyword)
    if not action.ok:
        st.warning(action.describe() + "（截图仍会继续，可能包含前端页面本身）")
    if maximize_hint and not action.ok:
        st.info("提示：请确认目标页面窗口已最大化，否则截图会包含桌面其它内容。")
    try:
        time.sleep(max(0.0, STABILIZE_SECONDS))  # 原有固定等待，保证页面渲染完成
        width, height = driver.size()
        frame = driver.capture((0, 0, width, height), label="fullscreen")
    finally:
        if action.ok and action.target is not None:
            action.restored = restore_window(action.target.hwnd)
            _remember_window_action(action, keyword)
    return frame, action


def _store_capture(frame, action: WindowAction, keyword: str = "") -> CaptureResult:
    """保存原图 + 生成展示副本，并重置与框选相关的会话状态。"""

    UI_DIR.mkdir(parents=True, exist_ok=True)
    image = Image.fromarray(frame.image)
    image.save(ORIGINAL_SCREENSHOT)

    mapping = compute_display_size(
        image.width,
        image.height,
        int(st.session_state["display_max_width"]),
        int(st.session_state["display_max_height"]),
    )[2]
    display = image if mapping.is_identity else image.resize(
        (mapping.display_width, mapping.display_height), Image.Resampling.LANCZOS
    )
    _write_display_copy(
        display, DISPLAY_SCREENSHOT, mapping, int(st.session_state["display_quality"])
    )

    previous = st.session_state.get("builder")
    if previous is None or (
        previous.screen.width != mapping.original_width
        or previous.screen.height != mapping.original_height
    ):
        st.session_state["builder"] = PlanBuilder(mapping.original_width, mapping.original_height)

    st.session_state["screenshot_path"] = str(ORIGINAL_SCREENSHOT)
    st.session_state["display_mapping"] = mapping_to_dict(mapping)
    _remember_window_action(action, keyword)
    st.session_state["last_pick_signature"] = None
    st.session_state["pending_point"] = None
    st.session_state["picker_nonce"] = int(st.session_state["picker_nonce"]) + 1
    return CaptureResult(
        width=mapping.original_width,
        height=mapping.original_height,
        target=action.target.title if action.ok and action.target else None,
        mapping=mapping,
        display_path=DISPLAY_SCREENSHOT,
    )


def _begin_capture(
    mode: str,
    window_title_keyword: str,
    node_title: str,
    countdown_seconds: int,
    page_keyword: str | None,
) -> None:
    """点击“截取首屏”后调用：最小化模式立即截图；倒计时模式进入等待阶段。"""

    keyword = _clean_title_keyword(window_title_keyword)
    if mode == WIN_MODE_MINIMIZE:
        try:
            frame, action = _capture_fullscreen(mode, keyword, node_title, page_keyword)
        except Exception as exc:  # noqa: BLE001
            st.error(f"截图失败：{exc}")
            st.info("可点击右侧“载入示例截图”先体验框选与配置流程。")
            return
        result = _store_capture(frame, action, keyword)
        st.session_state["countdown"]["active"] = False
        st.success(f"已截取首屏（{result.width}×{result.height}）：{result.mapping.scale:.3f}× 缩放展示")
        if not action.ok:
            st.warning(action.describe())
        st.rerun()

    st.session_state["countdown"] = {
        "active": True,
        "mode": "countdown",
        "seconds": int(countdown_seconds),
        "remaining": int(countdown_seconds),
        "target": None,
        "page_keyword": page_keyword,
        "window_keyword": keyword,
        "stabilize": STABILIZE_SECONDS,
    }
    st.session_state["window_action"] = None
    st.rerun()


def _run_countdown_frame(countdown: dict) -> bool:
    """执行倒计时的一帧；返回 True 表示倒计时刚结束（调用方随后截图）。"""

    remaining = int(countdown.get("remaining", 0))
    placeholder = st.empty()
    if remaining > 0:
        placeholder.warning(
            f"倒计时 {remaining} 秒：请立即切换到目标页面（建议最大化浏览器窗口），"
            "结束后自动截图；期间不会最小化或激活任何窗口。"
        )
        time.sleep(1.0)
        countdown["remaining"] = remaining - 1
        st.session_state["countdown"] = countdown
        return False
    placeholder.info("倒计时结束，正在截图……（等待页面稳定）")
    return True


def _finish_countdown(countdown: dict, window_title_keyword: str, node_title: str) -> None:
    """倒计时结束：先打印本次实际使用的关键字，再按倒计时方式截图（不做窗口操作）。"""

    keyword = _clean_title_keyword(
        countdown.get("window_keyword", window_title_keyword)
    )
    try:
        frame, action = _capture_fullscreen(
            WIN_MODE_COUNTDOWN, keyword, node_title, countdown.get("page_keyword")
        )
    except Exception as exc:  # noqa: BLE001
        st.session_state["countdown"]["active"] = False
        st.error(f"截图失败：{exc}")
        return
    result = _store_capture(frame, action, keyword)
    st.session_state["countdown"]["active"] = False
    st.success(
        f"已截取首屏（{result.width}×{result.height}，倒计时模式）："
        f"{describe_mapping(result.mapping)}"
    )
    st.rerun()


def _prepare_view_image(
    source: Path,
    builder: PlanBuilder,
    mapping: DisplayMapping,
    max_width: int,
    max_height: int,
    quality: int,
) -> tuple[Path, DisplayMapping, bool]:
    """按当前展示尺寸生成“原图叠加选框后再缩放”的画面，返回 (路径, 新映射, 映射是否变化)。

    选框始终使用原图坐标绘制、再整体缩放，因此任何展示尺寸下都不会出现坐标错位。

    注意：``source`` 是固定的 ``first_screen.png``，每次截图都会原地覆盖。若签名只用路径，
    覆盖后签名不变、旧的 ``view_*.jpg`` 会被当成有效缓存复用，前端就永远显示第一张图。
    因此签名必须包含**画面内容摘要**：同内容只存一份，内容一旦变化必然产生新文件名。
    """

    # 内容指纹：直接摘要源文件字节，截图/示例图覆盖后自然改变，读取成本可接受
    try:
        content_digest = hashlib.md5(source.read_bytes()).hexdigest()[:8]
    except OSError:
        # 文件暂不可读时退化为按修改时间，避免整个画布无法渲染
        content_digest = f"mtime{source.stat().st_mtime_ns if source.exists() else 0}"

    with Image.open(source) as image:
        image.load()
        regions = [Region.from_list(name, coords) for name, coords in builder.regions.items()]
        composed = render_regions(image, regions) if regions else image.convert("RGB")
        display_width, display_height, fresh_mapping = compute_display_size(
            composed.width, composed.height, max_width, max_height
        )
        if fresh_mapping.is_identity:
            display_image = composed
        else:
            display_image = composed.resize(
                (display_width, display_height), Image.Resampling.LANCZOS
            )
        signature = hashlib.md5(
            (
                f"{content_digest}|{builder.regions}|{fresh_mapping.display_width}x"
                f"{fresh_mapping.display_height}|{quality}"
            ).encode("utf-8")
        ).hexdigest()[:12]
        target = UI_DIR / f"view_{signature}.jpg"
        if not target.is_file():
            _write_display_copy(display_image, target, fresh_mapping, quality)
        changed = (
            fresh_mapping.display_width != mapping.display_width
            or fresh_mapping.display_height != mapping.display_height
        )
        return target, fresh_mapping, changed


def _restart_to_blank_screen() -> None:
    """清空区域但保留当前截图与展示设置（不重启整个会话，避免丢失配置）。"""

    previous = st.session_state.get("builder")
    if previous is None:
        return
    st.session_state["builder"] = PlanBuilder(previous.screen.width, previous.screen.height)
    st.session_state["last_pick_signature"] = None
    st.session_state["pending_point"] = None
    st.session_state["picker_nonce"] = int(st.session_state["picker_nonce"]) + 1
    st.rerun()


def _reset_ui_with_builder(
    builder: PlanBuilder,
    screenshot_path: str | Path | None,
    *,
    display_mapping: DisplayMapping | None = None,
) -> None:
    """结构性变更（换截图尺寸、加载示例）后重建控件状态，避免旧控件值覆盖新配置。"""

    st.session_state.clear()
    _init_state()
    # Streamlit 会按 key 保留控件旧值；结构性变更时必须显式重置，否则旧选项会覆盖新配置
    st.session_state["opt_quality"] = _quality_label(st.session_state["display_quality"])
    st.session_state["opt_max_width"] = int(st.session_state["display_max_width"])
    st.session_state["opt_max_height"] = int(st.session_state["display_max_height"])
    st.session_state["capture_window_mode"] = WIN_MODE_MINIMIZE
    st.session_state["capture_countdown_seconds"] = DEFAULT_COUNTDOWN_SECONDS
    st.session_state["builder"] = builder
    st.session_state["screenshot_path"] = (
        str(screenshot_path) if screenshot_path is not None else None
    )
    if display_mapping is not None:
        st.session_state["display_mapping"] = mapping_to_dict(display_mapping)
    st.session_state["picker_nonce"] = 1
    st.session_state["last_pick_signature"] = None
    st.rerun()


def _component_supports_drag() -> bool:
    try:
        signature = inspect.signature(streamlit_image_coordinates)
    except (TypeError, ValueError):
        return False
    return "click_and_drag" in signature.parameters


def _pick(image_path: Path, key: str, drag: bool):
    kwargs = {}
    if _component_supports_drag():
        kwargs["click_and_drag"] = drag
    try:
        return streamlit_image_coordinates(str(image_path), key=key, **kwargs)
    except TypeError:
        # 组件版本与签名探测不一致时，退回最基础的点击模式
        kwargs.pop("click_and_drag", None)
        return streamlit_image_coordinates(str(image_path), key=key, **kwargs)


def _normalize_pick(value) -> tuple[int, int, int, int] | None:
    if not isinstance(value, dict):
        return None
    if all(key in value for key in ("x1", "y1", "x2", "y2")):
        x1, y1, x2, y2 = (int(value["x1"]), int(value["y1"]), int(value["x2"]), int(value["y2"]))
        return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)
    if "x" in value and "y" in value:
        x, y = int(value["x"]), int(value["y"])
        return x, y, x, y
    return None


def _start_run(plan, run_dir: Path) -> subprocess.Popen:
    plan_path = PLANS_DIR / f"plan_{time.strftime('%Y%m%d-%H%M%S')}.json"
    save_plan(plan, plan_path)
    run_dir.mkdir(parents=True, exist_ok=True)
    console = (run_dir / "console.log").open("w", encoding="utf-8")
    command = [
        sys.executable,
        str(ROOT / "run.py"),
        "run",
        "--plan",
        str(plan_path),
        "--run-dir",
        str(run_dir),
        "--log-level",
        "INFO",
    ]
    # 子进程一律使用隐藏窗口，避免弹出/前置任何命令行窗口
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        command,
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        stdout=console,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=creationflags,
    )
    st.session_state["plan_path"] = str(plan_path)
    return process


def _stop_process(process) -> None:
    if process is None:
        return
    try:
        if process.poll() is None:
            process.terminate()
    except Exception as exc:  # pragma: no cover
        st.warning(f"停止进程失败：{exc}")


_init_state()


# ---------------------------------------------------------------------- #
# 页面标题与总览
# ---------------------------------------------------------------------- #
st.title("智能招聘数据抓取与可视化平台")
st.caption(
    "基于 NEWREADME.MD（IRDV-DOC-001 v1.0）实现：区域框选 + 双模式自动化 + OCR + 视觉大模型确认/去噪 + JSONL 增量落盘。"
)

builder: PlanBuilder | None = st.session_state.get("builder")
screenshot_path: Path | None = (
    Path(st.session_state["screenshot_path"]) if st.session_state.get("screenshot_path") else None
)

with st.sidebar:
    st.header("当前状态")
    if builder is None:
        st.info("尚未加载截图")
    else:
        st.success(f"已加载 {builder.screen.width}x{builder.screen.height}")
        st.write(f"区域数：{len(builder.regions)}")
        for name, coords in builder.regions.items():
            mode = builder.configs[name].get("mode")
            st.write(f"**{name}** {'短信模式' if mode == MODE_SMS else '抄录模式'} {coords}")
    st.divider()
    st.warning("运行真实采集时请勿触碰鼠标键盘；把鼠标移到屏幕左上角可急停。")
    st.caption("建议先运行“演示模式”验证界面与输出，再配置真实页面。")

tab_capture, tab_config, tab_scroll, tab_run, tab_results = st.tabs(
    ["① 截图与框选", "② 区域配置", "③ 滚动测试", "④ 运行采集", "⑤ 结果查看"]
)


# ---------------------------------------------------------------------- #
# ① 截图与框选
# ---------------------------------------------------------------------- #
with tab_capture:
    st.subheader("获取首屏截图")
    page_keyword_available = _current_route_keyword()
    col_capture, col_sample = st.columns([3, 2])
    with col_capture:
        # 浏览器窗口内的页面无法通过“窗口标题”区分（标题只是标签页名），
        # 因此前端默认走倒计时模式；自动最小化更适合“独立应用窗口”，
        # 或浏览器标题里确实包含可填写关键字的场景（此时关键字才是主匹配条件）。
        window_mode = st.radio(
            "窗口处理方式",
            WIN_MODE_CHOICES,
            index=WIN_MODE_CHOICES.index(
                _select_value(
                    st.session_state.get("capture_window_mode"),
                    WIN_MODE_CHOICES,
                    "capture_window_mode",
                    WIN_MODE_CHOICES.index(WIN_MODE_COUNTDOWN),
                )
            ),
            horizontal=True,
            help=(
                "自动最小化：按下方“前端窗口标题关键字”定位并只最小化该前端窗口，"
                "截图后在后台恢复，不会影响命令行/终端；"
                "倒计时模式：不做任何窗口操作，倒计时结束后直接截图，请在此期间手动切到目标页面。"
            ),
        )
        # ?page= 只作为“标题命中关键字之后”的精确路由条件，不再成为独立识别方式
        api_mode = page_keyword_available is not None
        if api_mode:
            st.text_input(
                "URL ?page= 路由值（仅用于在标题命中后精确路由）",
                value=page_keyword_available or "",
                disabled=True,
            )
        # 窗口标题关键字是主匹配条件：两个入口共用同一个会话键，值始终一致
        window_title = _render_window_keyword_input(
            st, key="window_title_capture"
        )
        if api_mode:
            st.caption(
                "API 模式的 ?page= 只作为附加条件：窗口标题仍必须先命中上面的关键字，"
                "?page= 仅用于在多个匹配窗口中精确选择。"
            )
        raw_countdown = st.number_input(
            "倒计时秒数",
            min_value=1,
            max_value=60,
            value=_clock_value(
                st.session_state.get("capture_countdown_seconds"),
                "capture_countdown_seconds",
                DEFAULT_COUNTDOWN_SECONDS,
            ),
            step=1,
            help=(
                "仅在“倒计时模式”下生效：点击截取首屏后先显示倒计时，结束后（并固定等待 "
                f"{STABILIZE_SECONDS:g} 秒页面稳定）再执行截图识别。"
            ),
        )
        countdown_seconds = _number_value(
            raw_countdown, "capture_countdown_seconds", DEFAULT_COUNTDOWN_SECONDS
        )
        st.session_state["capture_countdown_seconds"] = countdown_seconds
        if window_mode == WIN_MODE_COUNTDOWN:
            st.caption(
                f"倒计时结束后会固定等待 {STABILIZE_SECONDS:g} 秒再截图；"
                "期间不会最小化、激活或前置任何窗口（包括命令行窗口）。"
            )
        else:
            st.caption(
                "只最小化“标题包含上述关键字”的前端窗口（不区分大小写）；"
                "找不到时不会最小化其它任何窗口，并在截图前提示具体原因。"
            )
        if window_mode == WIN_MODE_MINIMIZE and not window_title:
            st.warning(
                "尚未填写前端窗口标题关键字：自动最小化不会执行。"
                "可填写浏览器标题中的文字（例如“智能招聘数据抓取平台”），或改用倒计时模式。"
            )
        _render_window_diagnostic(window_title)

        # 倒计时进行中：阻断重复点击，逐帧刷新进度
        countdown = st.session_state.get("countdown") or {}
        if countdown.get("active"):
            st.info(f"倒计时进行中：剩余约 {countdown.get('remaining', 0)} 秒")
            if _run_countdown_frame(countdown):
                _finish_countdown(countdown, window_title, STREAMLIT_NODE_TITLE)
            else:
                st.rerun()
            st.button("截取首屏", type="primary", disabled=True, key="capture_btn_busy")
        elif st.button("截取首屏", type="primary", key="capture_btn"):
            _begin_capture(
                window_mode,
                window_title,
                STREAMLIT_NODE_TITLE,
                int(countdown_seconds),
                page_keyword_available if api_mode else None,
            )
    with col_sample:
        st.write("没有可用屏幕或想离线体验？")
        if st.button("载入示例截图（1920×1080）"):
            if not SAMPLE_SCREENSHOT.is_file():
                st.error(f"示例截图不存在：{SAMPLE_SCREENSHOT}")
            else:
                with st.spinner("正在压缩示例截图用于展示……"):
                    UI_DIR.mkdir(parents=True, exist_ok=True)
                    ORIGINAL_SCREENSHOT.write_bytes(SAMPLE_SCREENSHOT.read_bytes())
                    with Image.open(ORIGINAL_SCREENSHOT) as image:
                        preview = image.convert("RGB")
                        display_mapping = compute_display_size(
                            preview.width,
                            preview.height,
                            int(st.session_state["display_max_width"]),
                            int(st.session_state["display_max_height"]),
                        )[2]
                        if not display_mapping.is_identity:
                            preview = preview.resize(
                                (display_mapping.display_width, display_mapping.display_height),
                                Image.Resampling.LANCZOS,
                            )
                        _write_display_copy(
                            preview,
                            DISPLAY_SCREENSHOT,
                            display_mapping,
                            int(st.session_state["display_quality"]),
                        )
                    st.session_state["display_mapping"] = mapping_to_dict(display_mapping)
                    st.session_state["screenshot_path"] = str(ORIGINAL_SCREENSHOT)
                    st.session_state["window_action"] = None
                    sample_builder = PlanBuilder(
                        display_mapping.original_width, display_mapping.original_height
                    )
                    _reset_ui_with_builder(
                        sample_builder,
                        ORIGINAL_SCREENSHOT,
                        display_mapping=display_mapping,
                    )

    builder = st.session_state.get("builder")
    original_path = (
        Path(st.session_state["screenshot_path"])
        if st.session_state.get("screenshot_path")
        else None
    )
    mapping_dict = st.session_state.get("display_mapping")
    window_action = st.session_state.get("window_action")

    # 窗口操作结果（含失败原因）在看到截图之前就展示，避免“静默失败”
    if window_action:
        if window_action.get("ok"):
            st.success(window_action.get("message", ""))
        else:
            st.warning(window_action.get("message", "窗口操作未执行：原因未知"))
        if window_action.get("keyword") is not None:
            st.caption(f"本次使用的窗口标题关键字：{window_action.get('keyword') or '（空）'}")

    if builder is None or original_path is None or not original_path.is_file() or not mapping_dict:
        st.info("请先截取首屏或载入示例截图。")
    else:
        mapping = mapping_from_dict(mapping_dict)

        st.divider()
        st.subheader("框选区域")
        st.caption(describe_mapping(mapping))
        col_w, col_h, col_q = st.columns([1, 1, 2])
        new_max_width = _number_value(
            col_w.number_input(
                "展示最大宽度(px)",
                min_value=480,
                max_value=3840,
                value=int(st.session_state["display_max_width"]),
                step=80,
            ),
            "opt_max_width",
            int(st.session_state["display_max_width"]),
        )
        new_max_height = _number_value(
            col_h.number_input(
                "展示最大高度(px)",
                min_value=320,
                max_value=2160,
                value=int(st.session_state["display_max_height"]),
                step=40,
            ),
            "opt_max_height",
            int(st.session_state["display_max_height"]),
        )
        quality_choices = list(DISPLAY_QUALITY_CHOICES)
        stored_quality = st.session_state.get("opt_quality")
        quality_index = (
            quality_choices.index(stored_quality)
            if isinstance(stored_quality, str) and stored_quality in quality_choices
            else 1
        )
        quality_label = _select_value(
            col_q.selectbox(
                "展示图片质量（仅影响前端加载速度，不影响坐标）",
                quality_choices,
                index=quality_index,
            ),
            quality_choices,
            "opt_quality",
            1,
        )
        st.session_state["display_max_width"] = int(new_max_width)
        st.session_state["display_max_height"] = int(new_max_height)
        st.session_state["display_quality"] = DISPLAY_QUALITY_CHOICES[quality_label]

        if _component_supports_drag():
            st.caption(
                "在图上拖拽即可新增区域：“显示坐标”会按缩放比例还原为原图像素坐标后写入区域字典。"
            )
        else:
            st.caption(
                "当前组件版本不支持拖拽，改为“点击左上角 → 点击右下角”两次点击框选（坐标同样按比例还原）。"
            )

        measure_mode = st.toggle(
            "测距模式（两点拾取滚动距离）",
            value=st.session_state["measure_mode"],
            help="依次点击两点，把两点距离与起点坐标换算回原图像素后写入该区域的滚动配置。",
        )
        st.session_state["measure_mode"] = measure_mode
        measure_target = None
        if measure_mode:
            names = list(builder.regions)
            if names:
                stored_target = st.session_state.get("measure_target")
                measure_target = st.selectbox(
                    "应用到区域",
                    names,
                    index=(
                        names.index(stored_target)
                        if isinstance(stored_target, str) and stored_target in names
                        else 0
                    ),
                )
                st.session_state["measure_target"] = measure_target
            else:
                st.info("请先框选至少一个区域。")
        else:
            st.session_state["measure_start"] = None

        supports_drag = _component_supports_drag()
        view_path, fresh_mapping, mapping_changed = _prepare_view_image(
            original_path,
            builder,
            mapping,
            int(st.session_state["display_max_width"]),
            int(st.session_state["display_max_height"]),
            int(st.session_state["display_quality"]),
        )
        if mapping_changed:
            # 展示尺寸变化后，组件返回的坐标基准也随之变化：先更新映射再接收下一次点击
            st.session_state["display_mapping"] = mapping_to_dict(fresh_mapping)
            st.session_state["last_pick_signature"] = None
            st.session_state["pending_point"] = None
            st.rerun()

        pick = _pick(
            view_path,
            key=f"region_picker_{st.session_state['picker_nonce']}",
            drag=supports_drag and not measure_mode,
        )
        rect = _normalize_pick(pick)

        if rect is not None:
            x1, y1, x2, y2 = rect
            signature = (x1, y1, x2, y2)
            if signature != st.session_state["last_pick_signature"]:
                st.session_state["last_pick_signature"] = signature
                is_drag = supports_drag and (x2 - x1 > 3 or y2 - y1 > 3)
                # 关键换算：显示像素 → 原图像素（保持宽高比时 x/y 缩放系数一致）
                ox1, oy1, ox2, oy2 = mapping.to_original_rect(x1, y1, x2, y2)

                if measure_mode and measure_target:
                    if is_drag:
                        start_point: list[int] | None = [ox1, oy1]
                        end_point: list[int] | None = [ox2, oy2]
                    elif st.session_state["measure_start"] is None:
                        st.session_state["measure_start"] = [ox1, oy1]
                        st.info(f"已拾取第一点：原图 ({ox1}, {oy1})，请点击第二点。")
                        start_point = end_point = None
                    else:
                        start_point = st.session_state["measure_start"]
                        end_point = [ox1, oy1]
                        st.session_state["measure_start"] = None
                    if start_point is not None and end_point is not None:
                        dy = abs(end_point[1] - start_point[1])
                        dx = abs(end_point[0] - start_point[0])
                        distance = dy if dy >= dx else dx
                        if distance < 10:
                            st.warning("两点距离过小，请重新拾取。")
                            st.session_state["measure_start"] = None
                        else:
                            builder.update_config(
                                measure_target,
                                {
                                    "scroll_start": [start_point[0], start_point[1]],
                                    "scroll_distance": -int(distance),
                                },
                            )
                            st.success(
                                f"已写入区域 {measure_target}：滚动距离 -{distance}，"
                                f"起始坐标 {start_point}"
                            )
                            st.session_state["picker_nonce"] += 1
                            st.session_state["last_pick_signature"] = None
                            st.rerun()

                elif not measure_mode:
                    if is_drag:
                        if (ox2 - ox1) >= 10 and (oy2 - oy1) >= 10:
                            try:
                                name = builder.add_region([ox1, oy1, ox2, oy2])
                                st.session_state["picker_nonce"] += 1
                                st.session_state["last_pick_signature"] = None
                                st.success(f"已新增区域 {name}（原图坐标）：{[ox1, oy1, ox2, oy2]}")
                                st.rerun()
                            except ConfigError as exc:
                                st.error(str(exc))
                    else:
                        pending = st.session_state.get("pending_point")
                        if pending is None:
                            st.session_state["pending_point"] = [ox1, oy1]
                            st.info(f"已拾取左上角：原图 ({ox1}, {oy1})，请点击右下角。")
                        else:
                            coords = [
                                min(pending[0], ox1),
                                min(pending[1], oy1),
                                max(pending[0], ox1),
                                max(pending[1], oy1),
                            ]
                            st.session_state["pending_point"] = None
                            if coords[2] - coords[0] < 10 or coords[3] - coords[1] < 10:
                                st.warning("框选范围过小，请重新选择两个点。")
                            else:
                                try:
                                    name = builder.add_region(coords)
                                    st.session_state["picker_nonce"] += 1
                                    st.session_state["last_pick_signature"] = None
                                    st.success(f"已新增区域 {name}（原图坐标）：{coords}")
                                    st.rerun()
                                except ConfigError as exc:
                                    st.error(str(exc))

        if builder.regions:
            st.write("当前区域（原图像素坐标，可直接修改）：")
            geometry = ScreenGeometry(
                builder.screen.width,
                builder.screen.height,
                builder.screen.screenshot_width,
                builder.screen.screenshot_height,
            )
            for name in list(builder.regions):
                coords = builder.regions[name]
                cols = st.columns([0.7, 1.1, 1.1, 1.1, 1.1, 1.2, 0.7])
                cols[0].markdown(f"### {name}")
                new_values = [
                    cols[1].number_input(
                        f"{name} xmin", value=int(coords[0]), min_value=0
                    ),
                    cols[2].number_input(
                        f"{name} ymin", value=int(coords[1]), min_value=0
                    ),
                    cols[3].number_input(
                        f"{name} xmax", value=int(coords[2]), min_value=0
                    ),
                    cols[4].number_input(
                        f"{name} ymax", value=int(coords[3]), min_value=0
                    ),
                ]
                mode = builder.configs[name].get("mode")
                cols[5].write("短信模式" if mode == MODE_SMS else "抄录模式")
                if cols[6].button("删除", key=f"del_{name}"):
                    builder.remove_region(name)
                    st.session_state["last_pick_signature"] = None
                    st.session_state["picker_nonce"] += 1
                    st.rerun()
                if new_values != coords:
                    try:
                        builder.set_region_coords(name, new_values)
                    except ConfigError as exc:
                        st.error(str(exc))
                if mapping.is_identity:
                    st.caption(
                        f"区域 {name} 起始点屏幕坐标："
                        f"{list(geometry.to_screen_point(coords[0], coords[1]))}"
                    )
            if st.button("清空全部区域"):
                _restart_to_blank_screen()


# ---------------------------------------------------------------------- #
# ② 区域配置
# ---------------------------------------------------------------------- #
with tab_config:
    builder = st.session_state.get("builder")
    if builder is None or not builder.regions:
        st.info("请先在“① 截图与框选”中框选区域。")
    else:
        st.subheader("区域工作模式与参数（NEWREADME 5.3.2）")
        st.caption(
            "坐标均为**原图像素**坐标；“滚动起始坐标”按原图坐标填写（与“① 测距模式”口径一致），"
            "运行时会按屏幕比例换算为鼠标坐标。"
        )
        if st.button("加载示例配置（A 短信 / B、C 抄录）"):
            sample = load_plan(EXAMPLE_PLAN)
            loaded = PlanBuilder.from_plan(sample)
            loaded.screen = builder.screen
            loaded.regions = {
                name: coords for name, coords in sample.regions.items()
            }
            loaded.configs = {
                name: cfg.to_dict() for name, cfg in sample.region_configs.items()
            }
            loaded.title_region = sample.output.title_region
            loaded.desc_region = sample.output.desc_region
            st.session_state["builder"] = loaded
            st.rerun()

        builder = st.session_state["builder"]
        region_names = list(builder.regions)
        for name in region_names:
            cfg = builder.config(name)
            mode = cfg.get("mode", MODE_TRANSCRIBE)
            with st.expander(
                f"区域 {name} —— {'短信模式（目录列表）' if mode == MODE_SMS else '抄录模式（文本块）'}",
                expanded=True,
            ):
                # 无 key 时，控件元素 ID 由 label 等参数决定；同一循环里重复的 label
                # 会触发 StreamlitDuplicateElementId。故所有 label 统一带区域名，
                # 既保证控件 ID 唯一，又让用户在界面上看到每个字段的含义。
                mode_label = st.radio(
                    f"{name} 工作模式",
                    ["短信模式", "抄录模式"],
                    index=0 if mode == MODE_SMS else 1,
                    horizontal=True,
                )
                desired_mode = MODE_SMS if mode_label == "短信模式" else MODE_TRANSCRIBE
                if desired_mode != mode:
                    builder.set_mode(name, desired_mode)
                    st.rerun()

                if desired_mode == MODE_SMS:
                    scroll_distance = st.number_input(
                        f"{name} 滚动距离（负值向下）",
                        value=int(cfg.get("scroll_distance") or -180),
                        step=10,
                    )
                    start_text = st.text_input(
                        f"{name} 滚动起始坐标 [x, y]",
                        value=_point_text(cfg.get("scroll_start")),
                    )
                    markers = st.text_input(
                        f"{name} 滚动停止条件（多个用逗号分隔）",
                        value="、".join(cfg.get("scroll_stop_markers") or []),
                    )
                    stop_match = st.selectbox(
                        f"{name} 停止条件匹配方式",
                        ["substring", "exact"],
                        index=0 if cfg.get("scroll_stop_match", "substring") == "substring" else 1,
                        help="默认按“包含”匹配，避免关键字与其它文本连写导致无法停止（修复 NEWREADME 9.4-3）。",
                    )
                    transcribe_options = [
                        item for item in region_names if builder.configs[item].get("mode") == MODE_TRANSCRIBE
                    ]
                    invoke = st.multiselect(
                        f"{name} 调用抄录模式的区域（按顺序调用）",
                        transcribe_options,
                        default=[item for item in cfg.get("invoke_regions", []) if item in transcribe_options],
                    )
                    structure_label = st.selectbox(
                        f"{name} 文本数据结构",
                        ["字典模式", "集合模式"],
                        index=0 if cfg.get("data_structure") == "dict" else 1,
                    )
                    structure = "dict" if structure_label == "字典模式" else "set"
                    key_region = None
                    if structure == "dict":
                        key_options = ["（未选择）"] + invoke
                        current_key = cfg.get("key_region")
                        key_region = st.selectbox(
                            f"{name} 做键的抄录区域（结果须可哈希）",
                            key_options,
                            index=key_options.index(current_key) if current_key in key_options else 0,
                        )
                        key_region = None if key_region == "（未选择）" else key_region
                    max_items = st.number_input(
                        f"{name} 本区域份数上限（0 表示不单独限制）",
                        value=int(cfg.get("max_items") or 0),
                        min_value=0,
                        step=1,
                    )
                    verify_click = st.checkbox(
                        f"{name} 点击后由视觉大模型确认命中（默认关闭，见 D2）",
                        value=bool(cfg.get("verify_click")),
                    )
                    col_a, col_b = st.columns(2)
                    rollback_step = col_a.number_input(
                        f"{name} 回滚步长",
                        value=int(cfg.get("rollback_step", 50)),
                        min_value=1,
                    )
                    max_click_retries = col_b.number_input(
                        f"{name} 最大点击重试次数",
                        value=int(cfg.get("max_click_retries", 3)),
                        min_value=1,
                    )
                    max_rounds = st.number_input(
                        f"{name} 最大循环轮次（安全阀）",
                        value=int(cfg.get("max_rounds", 30)),
                        min_value=1,
                    )
                    start_point = _parse_point(start_text)
                    if start_text.strip() and start_point is None:
                        st.error("滚动起始坐标格式应为 x, y")
                    builder.update_config(
                        name,
                        {
                            "scroll_distance": int(scroll_distance),
                            "scroll_start": start_point,
                            "scroll_stop_markers": _parse_markers(markers),
                            "scroll_stop_match": stop_match,
                            "invoke_regions": invoke,
                            "data_structure": structure,
                            "key_region": key_region,
                            "max_items": int(max_items) or None,
                            "verify_click": verify_click,
                            "rollback_step": int(rollback_step),
                            "max_click_retries": int(max_click_retries),
                            "max_rounds": int(max_rounds),
                        },
                    )
                else:
                    scrollable = st.checkbox(
                        f"{name} 该区域可滚动",
                        value=bool(cfg.get("scrollable")),
                    )
                    scroll_distance = cfg.get("scroll_distance")
                    start_text = _point_text(cfg.get("scroll_start"))
                    markers_text = "、".join(cfg.get("scroll_stop_markers") or [])
                    stop_match = cfg.get("scroll_stop_match", "substring")
                    if scrollable:
                        scroll_distance = st.number_input(
                            f"{name} 滚动距离（负值向下）",
                            value=int(cfg.get("scroll_distance") or -180),
                            step=10,
                        )
                        start_text = st.text_input(
                            f"{name} 滚动起始坐标 [x, y]",
                            value=_point_text(cfg.get("scroll_start")),
                        )
                        markers_text = st.text_input(
                            f"{name} 滚动停止条件（多个用逗号分隔）",
                            value="、".join(cfg.get("scroll_stop_markers") or []),
                        )
                        stop_match = st.selectbox(
                            f"{name} 停止条件匹配方式",
                            ["substring", "exact"],
                            index=0 if cfg.get("scroll_stop_match", "substring") == "substring" else 1,
                        )
                    expected_content = st.text_input(
                        f"{name} 期望内容（语义提示，如“职位描述”）",
                        value=cfg.get("expected_content") or "",
                    )
                    format_label = st.selectbox(
                        f"{name} 期望格式",
                        ["list（列表）", "str（字符串）", "tuple（元组）"],
                        index={"list": 0, "str": 1, "tuple": 2}.get(
                            cfg.get("expected_format", "list"), 0
                        ),
                    )
                    expected_format = format_label.split("（")[0]
                    anchor_key = st.text_input(
                        f"{name} 首轮锚点关键字（可留空）",
                        value=cfg.get("anchor_key") or "",
                        help="例如职位描述区域填“职位描述”；首轮找不到时由视觉大模型确认（8.3.2）。",
                    )
                    max_rounds = st.number_input(
                        f"{name} 最大滚动轮次",
                        value=int(cfg.get("max_rounds", 12)),
                        min_value=1,
                    )
                    auto_adjust = st.checkbox(
                        f"{name} 锚点修正后自动应用建议滚动距离（默认关闭）",
                        value=bool(cfg.get("auto_adjust_scroll")),
                    )
                    start_point = _parse_point(start_text) if start_text.strip() else None
                    if scrollable and start_text.strip() and start_point is None:
                        st.error("滚动起始坐标格式应为 x, y")
                    builder.update_config(
                        name,
                        {
                            "scrollable": scrollable,
                            "scroll_distance": int(scroll_distance) if scrollable and scroll_distance is not None else None,
                            "scroll_start": start_point,
                            "scroll_stop_markers": _parse_markers(markers_text),
                            "scroll_stop_match": stop_match,
                            "expected_content": expected_content,
                            "expected_format": expected_format,
                            "anchor_key": anchor_key.strip() or None,
                            "max_rounds": int(max_rounds),
                            "auto_adjust_scroll": auto_adjust,
                        },
                    )

        st.divider()
        st.subheader("输出映射")
        st.caption("NEWREADME 7.3.2 要求输出 job_title / job_desc；这里指定用哪个区域做标题、哪个区域做描述。")
        options = ["（未设置）"] + region_names
        col_title, col_desc, col_auto = st.columns([2, 2, 1])
        title_index = (
            options.index(builder.title_region)
            if builder.title_region in options
            else 0
        )
        desc_index = (
            options.index(builder.desc_region)
            if builder.desc_region in options
            else 0
        )
        title_choice = col_title.selectbox("标题区域", options, index=title_index)
        desc_choice = col_desc.selectbox("描述区域", options, index=desc_index)
        if col_auto.button("自动推荐"):
            title_suggestion, desc_suggestion = builder.suggest_output_regions()
            builder.set_output_regions(title_suggestion, desc_suggestion)
            st.rerun()
        builder.set_output_regions(
            None if title_choice == "（未设置）" else title_choice,
            None if desc_choice == "（未设置）" else desc_choice,
        )


# ---------------------------------------------------------------------- #
# ③ 滚动测试
# ---------------------------------------------------------------------- #
with tab_scroll:
    st.subheader("滚动距离测试（FR-04）")
    st.caption("点击开始后立即切换到目标页面；测试会在服务端操作真实鼠标。")
    builder = st.session_state.get("builder")
    if builder is None or not builder.regions:
        st.info("请先框选区域。")
    else:
        names = list(builder.regions)
        stored_region = st.session_state.get("scroll_region")
        region_name = st.selectbox(
            "测试区域",
            names,
            index=(
                names.index(stored_region)
                if isinstance(stored_region, str) and stored_region in names
                else 0
            ),
        )
        st.session_state["scroll_region"] = region_name
        cfg = builder.config(region_name)
        default_start = cfg.get("scroll_start") or [400, 500]
        col_x, col_y, col_d, col_t = st.columns(4)
        x = col_x.number_input("x", value=int(default_start[0]), min_value=0)
        y = col_y.number_input("y", value=int(default_start[1]), min_value=0)
        distance = col_d.number_input(
            "滚动距离", value=int(cfg.get("scroll_distance") or -180), step=10
        )
        times = col_t.number_input("次数", value=5, min_value=1)
        col_i, col_delay, col_btn = st.columns([1, 1, 2])
        interval = col_i.number_input("间隔（秒）", value=0.5, min_value=0.1, step=0.1)
        delay = col_delay.number_input("倒计时（秒）", value=3, min_value=0)
        if col_btn.button("开始滚动测试", type="primary"):
            UI_DIR.mkdir(parents=True, exist_ok=True)
            log_path = UI_DIR / "scroll_test.log"
            console = log_path.open("w", encoding="utf-8")
            command = [
                sys.executable,
                str(ROOT / "run.py"),
                "scroll-test",
                "--x",
                str(int(x)),
                "--y",
                str(int(y)),
                "--distance",
                str(int(distance)),
                "--times",
                str(int(times)),
                "--interval",
                str(float(interval)),
                "--delay",
                str(float(delay)),
            ]
            st.session_state["scroll_proc"] = subprocess.Popen(
                command,
                cwd=str(ROOT),
                stdin=subprocess.DEVNULL,
                stdout=console,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            st.success("滚动测试已启动，请立即切换到目标页面。")
        st.write("测试日志：")
        st.code(_tail(UI_DIR / "scroll_test.log") or "（暂无日志）", language="text")


# ---------------------------------------------------------------------- #
# ④ 运行采集
# ---------------------------------------------------------------------- #
with tab_run:
    st.subheader("生成计划并启动后端采集")
    builder = st.session_state.get("builder")
    if builder is None or not builder.regions:
        st.info("请先框选区域并完成配置。")
    else:
        col_engine, col_llm, col_max = st.columns(3)
        engine = col_engine.selectbox(
            "屏幕/OCR 引擎",
            ["mock", "real"],
            index=0,
            help="mock：不移动鼠标，用场景剧本演示完整链路；real：pyautogui + PaddleOCR。",
        )
        llm = col_llm.selectbox(
            "视觉大模型",
            ["mock", "deepseek"],
            index=0,
            help="deepseek 需要环境变量 DEEPSEEK_API_KEY。",
        )
        max_jobs = col_max.number_input("采集份数上限", value=10, min_value=1, step=1)
        col_speed, col_out = st.columns(2)
        output_dir = col_speed.text_input("输出目录", value="runtime/output")
        start_delay = col_out.number_input(
            "启动倒计时（秒）", value=3 if engine == "real" else 0, min_value=0
        )
        minimize = st.checkbox(
            "启动后自动最小化前端窗口（真实模式）", value=engine == "real"
        )
        # 与“① 截图与框选”共用同一个关键字状态，一处修改两处同步
        run_window_title = _render_window_keyword_input(st, key="window_title_run")

        build_kwargs = dict(
            max_jobs=int(max_jobs),
            engine=engine,
            llm=llm,
            output_dir=output_dir,
            minimize_frontend=minimize,
            window_title=run_window_title,
            start_delay_seconds=int(start_delay),
        )
        plan, errors = builder.try_build(**build_kwargs)
        if errors:
            st.error("计划校验未通过：")
            for error in errors:
                st.write(f"- {error}")
        else:
            with st.expander("查看计划 JSON", expanded=False):
                st.json(plan_to_dict(plan))
            col_save, col_start, col_manage = st.columns([1, 1, 2])
            if col_save.button("保存计划到 runtime/plans"):
                path = PLANS_DIR / f"plan_{time.strftime('%Y%m%d-%H%M%S')}.json"
                save_plan(plan, path)
                st.session_state["plan_path"] = str(path)
                st.success(f"已保存：{path}")
            if col_start.button("启动采集", type="primary"):
                run_dir = create_run_dir(RUNS_DIR, prefix="ui")
                process = _start_run(plan, run_dir)
                st.session_state["run_proc"] = process
                st.session_state["run_dir"] = str(run_dir)
                st.warning("已启动。真实模式下请立即切换到目标页面，运行期间不要触碰鼠标键盘。")
                st.rerun()
            st.download_button(
                "下载计划 JSON",
                data=json.dumps(plan_to_dict(plan), ensure_ascii=False, indent=2),
                file_name="capture_plan.json",
                mime="application/json",
            )

        run_dir_value = st.session_state.get("run_dir")
        process = st.session_state.get("run_proc")
        if run_dir_value:
            run_dir = Path(run_dir_value)
            st.divider()
            st.subheader("运行状态")
            status = "运行中" if process is not None and process.poll() is None else "已结束"
            col_status, col_refresh, col_stop = st.columns([2, 1, 1])
            col_status.write(f"状态：**{status}**；PID：{getattr(process, 'pid', '—')}")
            col_status.write(f"运行目录：`{run_dir}`")
            col_refresh.button("刷新日志/计数", key="refresh_run")
            if col_stop.button("停止运行", key="stop_run"):
                _stop_process(process)
                st.warning("已发送停止指令；已采集数据会保留在 jobs.jsonl 中。")
            st.write(f"已落盘：{_count_jsonl(run_dir / 'jobs.jsonl')} 份")
            tab_log, tab_console = st.tabs(["run.log", "console.log"])
            with tab_log:
                st.code(_tail(run_dir / "run.log", 120) or "（暂无日志）", language="text")
            with tab_console:
                st.code(_tail(run_dir / "console.log", 80) or "（暂无日志）", language="text")
            if process is not None and process.poll() is not None:
                report = read_report(run_dir)
                if report:
                    st.info(
                        f"结束原因：{report.get('stop_reason')} —— {report.get('stop_message')}"
                    )


# ---------------------------------------------------------------------- #
# ⑤ 结果查看（M5 预览，附加）
# ---------------------------------------------------------------------- #
with tab_results:
    st.subheader("采集结果查看")
    st.caption("这是 NEWREADME 第 13 章 M5 的只读预览（附加项，不属于本期设计范围）。")
    runs = list_runs(RUNS_DIR)
    if not runs:
        st.info("暂无运行记录。先运行演示模式或真实采集。")
    else:
        labels = [run.name for run in runs]
        stored_run = st.session_state.get("result_run")
        selected = st.selectbox(
            "选择运行目录",
            labels,
            index=(
                labels.index(stored_run)
                if isinstance(stored_run, str) and stored_run in labels
                else 0
            ),
        )
        st.session_state["result_run"] = selected
        run_dir = RUNS_DIR / selected
        report = read_report(run_dir)
        records = read_jsonl(run_dir / "jobs.jsonl")
        summary = summarize_records(records)
        col_1, col_2, col_3, col_4 = st.columns(4)
        col_1.metric("岗位数", summary["jobs"])
        col_2.metric("平均描述行数", summary["avg_desc_lines"])
        col_3.metric("最早采集", (summary["earliest"] or "—")[11:19] or "—")
        col_4.metric("最晚采集", (summary["latest"] or "—")[11:19] or "—")
        if report:
            st.caption(
                f"停止原因：{report.get('stop_reason')}；引擎：{report.get('engine')}；"
                f"大模型：{report.get('llm')}；耗时：{report.get('duration_seconds')}s"
            )
            with st.expander("运行报告"):
                st.json(report)
        st.download_button(
            "下载 jobs.jsonl",
            data="\n".join(json.dumps(item, ensure_ascii=False) for item in records),
            file_name="jobs.jsonl",
            mime="application/x-ndjson",
        )
        if not records:
            st.warning("该运行没有采集到岗位记录。")
        for index, record in enumerate(records, start=1):
            title = record.get("job_title", "（无标题）")
            with st.expander(f"{index}. {title}（{len(record.get('job_desc') or [])} 行）"):
                st.write(f"采集时间：{record.get('captured_at')}")
                if record.get("warnings"):
                    st.warning("；".join(record["warnings"]))
                for line_number, line in enumerate(record.get("job_desc") or [], start=1):
                    st.write(f"{line_number}. {line}")

st.divider()
st.caption(
    "已知限制：真实链路需要 Windows 桌面、1920×1080（或重新框选）与 DEEPSEEK_API_KEY；"
    "MySQL 入库与完整可视化看板按 NEWREADME 暂缓，详见 docs/REPORT.md。"
)
