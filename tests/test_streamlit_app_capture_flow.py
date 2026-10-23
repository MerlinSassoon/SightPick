"""前端端到端冒烟：用最小 Streamlit 替身驱动“截屏/框选”流程。

- 修复项③：在缩小展示图上框选的坐标必须精确还原为原图像素坐标；
- 修复项②：倒计时模式不做任何窗口操作，倒计时结束后才截图；
- 修复项①：最小化模式只作用于“定位到的前端窗口”，定位失败时不做任何窗口操作；
- 窗口关键字：用户输入作为主匹配条件、节点标题独立参数、空关键字不误伤、失败原因可见。
"""

from __future__ import annotations

import builtins
import runpy
import sys
import types
from pathlib import Path

import numpy as np
import pytest
from PIL import Image


class _FakeState(dict):
    def setdefault(self, key, default=None):
        return super().setdefault(key, default)


class _FakeCtx:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class _FakeStreamlit(types.ModuleType):
    """最小 Streamlit 替身：按钮/单选框/数字输入按测试预设返回。"""

    def __init__(self) -> None:
        super().__init__("streamlit")
        self.session_state = _FakeState()
        self.query_params = {}
        self.button_queue: list[bool] = []
        self.radio_queue: list[int] = []
        self.number_queue: list[float] = []
        self.text_queue: list[str] = []
        self.clicked: list[str] = []
        self.image_calls: list[dict] = []
        self.warnings: list[str] = []
        self.errors: list[str] = []
        self.rendered: list[str] = []
        self.text_inputs: list[dict] = []

    def __getattr__(self, name):
        if name == "session_state":
            return self.__dict__["session_state"]
        if name == "sidebar":
            return _FakeCtx()
        if name == "tabs":
            return lambda labels: [_FakeCtx() for _ in labels]
        if name == "columns":
            return lambda spec: [
                _FakeCtx() for _ in (spec if isinstance(spec, list) else [1] * spec)
            ]
        if name in ("expander", "spinner", "empty"):
            return lambda *args, **kwargs: _FakeCtx()
        if name == "button":
            def button(label, *args, **kwargs):
                clicked = self.button_queue.pop(0) if self.button_queue else False
                if clicked:
                    self.clicked.append(str(label))
                return clicked

            return button
        if name in ("checkbox", "toggle"):
            return lambda *args, **kwargs: False
        if name == "radio":
            def radio(label, options, *args, **kwargs):
                index = self.radio_queue.pop(0) if self.radio_queue else kwargs.get("index", 0)
                return options[index]

            return radio
        if name == "text_input":
            def text_input(label, *args, **kwargs):
                kwargs.setdefault("label", str(label))
                self.text_inputs.append(dict(kwargs))
                if self.text_queue:
                    return self.text_queue.pop(0)
                return kwargs.get("value", "")

            return text_input
        if name == "number_input":
            def number_input(label, *args, **kwargs):
                if self.number_queue:
                    return self.number_queue.pop(0)
                return kwargs.get("value", 0)

            return number_input
        if name == "selectbox":
            def selectbox(label, options, *args, **kwargs):
                return options[kwargs.get("index", 0)]

            return selectbox
        if name == "multiselect":
            return lambda *args, **kwargs: kwargs.get("default", [])
        if name == "warning":
            def warning(message, *args, **kwargs):
                self.warnings.append(str(message))

            return warning
        if name == "error":
            def error(message, *args, **kwargs):
                self.errors.append(str(message))

            return error
        if name == "rerun":
            def rerun():
                raise SystemExit("rerun")

            return rerun
        if name in ("info", "success", "caption"):
            def text(message="", *args, **kwargs):
                self.rendered.append(str(message))

            return text
        return lambda *args, **kwargs: None


def _install(monkeypatch, project_root: Path, picker):
    fake = _FakeStreamlit()
    monkeypatch.setitem(sys.modules, "streamlit", fake)
    component = types.ModuleType("streamlit_image_coordinates")

    def streamlit_image_coordinates(image, *, key=None, click_and_drag=False):
        fake.image_calls.append({"image": image, "key": key, "drag": click_and_drag})
        return picker(image)

    component.streamlit_image_coordinates = streamlit_image_coordinates
    monkeypatch.setitem(sys.modules, "streamlit_image_coordinates", component)
    monkeypatch.syspath_prepend(str(project_root / "src"))

    import recruiting_capture.storage.reader as reader

    monkeypatch.setattr(reader, "list_runs", lambda root: [])
    return fake


def _pick_everything(image):
    with Image.open(image) as shown:
        width, height = shown.size
    return {"x1": 0, "y1": 0, "x2": width - 1, "y2": height - 1}


def _run(project_root: Path, name: str, rounds: int = 4) -> list[str]:
    """执行一次前端脚本（含 Streamlit 的重跑循环），返回警告信息。"""

    warnings: list[str] = []
    namespace: dict = {}
    for _ in range(rounds):
        try:
            namespace = runpy.run_path(
                str(project_root / "app" / "streamlit_app.py"), run_name=name
            )
            break
        except SystemExit as exc:
            if str(exc) != "rerun":
                break
        warnings.extend(namespace.get("_smoke_warnings", []))
    return warnings


def test_selection_on_scaled_image_maps_to_original_pixels(monkeypatch, project_root: Path) -> None:
    fake = _install(monkeypatch, project_root, _pick_everything)
    # 按钮顺序：截取首屏(False) → 载入示例截图(True)；之后的轮次不再点击任何按钮
    fake.button_queue = [False, True, False, False]
    _run(project_root, "dsh_selection_smoke", rounds=5)
    builder = fake.session_state.get("builder")
    mapping = fake.session_state.get("display_mapping")
    assert builder is not None and mapping is not None
    assert mapping["display"][0] < mapping["original"][0], "默认应缩小展示以便一屏可见"
    scale = mapping["original"][0] / mapping["display"][0]
    assert "A" in builder.regions
    region = builder.regions["A"]
    # 拖到显示图右下角时，还原误差不超过一个显示像素对应的原图像素
    assert region[0] == 0 and region[1] == 0
    assert abs(region[2] - builder.screen.width) <= scale + 1
    assert abs(region[3] - builder.screen.height) <= scale + 1
    assert (builder.screen.width, builder.screen.height) == tuple(mapping["original"])


def test_partial_selection_is_not_offset(monkeypatch, project_root: Path) -> None:
    """验证“显示坐标 × 缩放倍数”而不是原样写入。"""

    shown_sizes: list[tuple[int, int]] = []

    def picker(image):
        with Image.open(image) as shown:
            shown_sizes.append(shown.size)
            width, height = shown.size
        # 左边缘半屏：显示坐标 (0,0) → (width/2, height)
        return {"x1": 0, "y1": 0, "x2": width // 2, "y2": height - 1}

    fake = _install(monkeypatch, project_root, picker)
    fake.button_queue = [False, True, False, False]
    _run(project_root, "dsh_selection_partial_smoke", rounds=5)

    builder = fake.session_state.get("builder")
    mapping = fake.session_state.get("display_mapping")
    assert builder is not None and mapping is not None
    scale = mapping["original"][0] / mapping["display"][0]
    region = builder.regions["A"]
    assert region[0] == 0 and region[1] == 0
    assert abs(region[2] - mapping["original"][0] / 2) <= scale + 1
    assert abs(region[3] - mapping["original"][1]) <= scale + 1


class _FakeDriver:
    def __init__(self) -> None:
        self.captures = 0

    def size(self):
        return (1920, 1080)

    def capture(self, rect, label=None):
        from recruiting_capture.capture.driver import Frame

        self.captures += 1
        array = np.zeros((1080, 1920, 3), dtype=np.uint8)
        return Frame(image=array, region=rect, label=label, meta={})


def _patch_capture(monkeypatch, project_root: Path, fake, driver):
    import recruiting_capture.capture.pyautogui_driver as driver_module

    monkeypatch.setattr(driver_module, "PyAutoGuiDriver", lambda *a, **k: driver)
    # app 在模块级 from ... import PyAutoGuiDriver，runpy 会重新导入模块，故直接替换模块属性即可


def test_countdown_mode_never_touches_windows(monkeypatch, project_root: Path) -> None:
    import recruiting_capture.capture.window as window_module

    calls: list[str] = []
    monkeypatch.setattr(window_module, "find_frontend_window", lambda *a, **k: calls.append("find"))
    monkeypatch.setattr(window_module, "minimize_window", lambda *a, **k: calls.append("minimize"))
    monkeypatch.setattr(window_module, "restore_window", lambda *a, **k: calls.append("restore"))

    driver = _FakeDriver()
    import recruiting_capture.capture.pyautogui_driver as driver_module

    monkeypatch.setattr(driver_module, "PyAutoGuiDriver", lambda *a, **k: driver)
    fake = _install(monkeypatch, project_root, lambda image: None)
    # 第一轮：点击“截取首屏”，radio 选倒计时模式，倒计时秒数 = 3
    fake.button_queue = [True, False, False]
    fake.radio_queue = [1]
    fake.number_queue = [3]
    _run(project_root, "dsh_countdown_start_smoke", rounds=1)

    countdown = fake.session_state.get("countdown")
    assert countdown is not None and countdown["active"] is True
    assert countdown["remaining"] == 3
    assert driver.captures == 0, "倒计时期间不应截图"
    assert calls == [], "倒计时模式不得调用任何窗口操作"

    # 第二轮：模拟倒计时逐帧推进到 0，然后截图
    fake.huifu = None
    countdown["remaining"] = 0
    fake.session_state["countdown"] = countdown
    fake.button_queue = [False, False, False]
    fake.radio_queue = []
    fake.number_queue = []
    _run(project_root, "dsh_countdown_finish_smoke", rounds=1)

    assert driver.captures == 1, "倒计时结束后应截图一次"
    assert fake.session_state["countdown"]["active"] is False
    assert calls == [], "倒计时模式全程不得触碰窗口"
    assert fake.session_state.get("display_mapping") is not None


def test_minimize_mode_uses_only_located_window(monkeypatch, project_root: Path) -> None:
    import recruiting_capture.capture.window as window_module
    from recruiting_capture.capture.window import WindowTarget

    calls: list[object] = []

    def fake_find(title_keyword="Streamlit", *, page_keyword=None, node_title=None):
        calls.append(("find", title_keyword, page_keyword, node_title))
        return WindowTarget(hwnd=4242, title="智能招聘 - Streamlit - Google Chrome")

    monkeypatch.setattr(window_module, "find_frontend_window", fake_find)
    monkeypatch.setattr(window_module, "minimize_window", lambda hwnd: calls.append(("min", hwnd)) or True)
    monkeypatch.setattr(window_module, "restore_window", lambda hwnd, **k: calls.append(("restore", hwnd)) or True)

    driver = _FakeDriver()
    import recruiting_capture.capture.pyautogui_driver as driver_module

    monkeypatch.setattr(driver_module, "PyAutoGuiDriver", lambda *a, **k: driver)
    fake = _install(monkeypatch, project_root, lambda image: None)
    fake.button_queue = [True]
    fake.radio_queue = [0]  # 自动最小化模式
    fake.number_queue = [5]
    fake.text_queue = ["  智能招聘  "]
    _run(project_root, "dsh_minimize_smoke")

    assert ("min", 4242) in calls and ("restore", 4242) in calls
    # 用户关键字必须被清洗后作为主匹配条件传入，节点标题单独传入
    assert ("find", "智能招聘", None, "Streamlit") in calls
    assert driver.captures == 1
    action = fake.session_state["window_action"]
    assert action["ok"] is True
    assert action["keyword"] == "智能招聘"
    assert action["restored"] is True


def test_minimize_mode_skips_when_window_not_found(monkeypatch, project_root: Path) -> None:
    import recruiting_capture.capture.window as window_module

    calls: list[str] = []
    monkeypatch.setattr(window_module, "find_frontend_window", lambda *a, **k: None)
    monkeypatch.setattr(window_module, "minimize_window", lambda *a, **k: calls.append("min") or True)
    monkeypatch.setattr(window_module, "restore_window", lambda *a, **k: calls.append("restore"))

    driver = _FakeDriver()
    import recruiting_capture.capture.pyautogui_driver as driver_module

    monkeypatch.setattr(driver_module, "PyAutoGuiDriver", lambda *a, **k: driver)
    fake = _install(monkeypatch, project_root, lambda image: None)
    fake.button_queue = [True]
    fake.radio_queue = [0]
    fake.number_queue = [5]
    fake.text_queue = ["不存在的窗口标题"]
    _run(project_root, "dsh_minimize_missing_smoke")

    assert calls == [], "定位失败时绝不能去最小化其它窗口"
    assert driver.captures == 1
    action = fake.session_state["window_action"]
    assert action["ok"] is False
    assert action["keyword"] == "不存在的窗口标题"
    assert "不存在的窗口标题" in action["reason"]
    # 失败原因必须在截图前/失败时就出现在界面上，而不是只在截图存在时才显示
    assert any("不存在的窗口标题" in message for message in fake.warnings)


def test_empty_keyword_never_attempts_minimize(monkeypatch, project_root: Path) -> None:
    """未填写关键字（或只填空格）时不得调用窗口定位，避免用节点标题误伤。"""

    import recruiting_capture.capture.window as window_module

    calls: list[str] = []
    monkeypatch.setattr(window_module, "find_frontend_window", lambda *a, **k: calls.append("find"))
    monkeypatch.setattr(window_module, "minimize_window", lambda *a, **k: calls.append("min") or True)
    monkeypatch.setattr(window_module, "restore_window", lambda *a, **k: calls.append("restore"))

    driver = _FakeDriver()
    import recruiting_capture.capture.pyautogui_driver as driver_module

    monkeypatch.setattr(driver_module, "PyAutoGuiDriver", lambda *a, **k: driver)
    fake = _install(monkeypatch, project_root, lambda image: None)
    fake.button_queue = [True]
    fake.radio_queue = [0]
    fake.number_queue = [5]
    fake.text_queue = ["   "]
    _run(project_root, "dsh_minimize_empty_keyword_smoke")

    assert calls == []
    assert driver.captures == 1
    action = fake.session_state["window_action"]
    assert action["ok"] is False
    assert action["keyword"] == ""
    assert "未填写" in action["reason"]


def test_app_does_not_import_pygetwindow(monkeypatch, project_root: Path) -> None:
    """生产路径不得依赖 pygetwindow（原调试打印会导致 ModuleNotFoundError）。"""

    import recruiting_capture.capture.window as window_module
    from recruiting_capture.capture.window import WindowTarget

    monkeypatch.setattr(
        window_module,
        "find_frontend_window",
        lambda *a, **k: WindowTarget(hwnd=1, title="x - Streamlit"),
    )
    monkeypatch.setattr(window_module, "minimize_window", lambda *a, **k: True)
    monkeypatch.setattr(window_module, "restore_window", lambda *a, **k: True)
    sys.modules.pop("pygetwindow", None)

    real_import = builtins.__import__
    imported: list[str] = []

    def guarded_import(name, *args, **kwargs):
        imported.append(name)
        if name.split(".")[0] == "pygetwindow":
            raise ModuleNotFoundError("No module named 'pygetwindow'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    driver = _FakeDriver()
    import recruiting_capture.capture.pyautogui_driver as driver_module

    monkeypatch.setattr(driver_module, "PyAutoGuiDriver", lambda *a, **k: driver)
    fake = _install(monkeypatch, project_root, lambda image: None)
    fake.button_queue = [True]
    fake.radio_queue = [0]
    fake.number_queue = [5]
    fake.text_queue = ["x"]
    _run(project_root, "dsh_no_pygetwindow_smoke")

    assert not any(name.split(".")[0] == "pygetwindow" for name in imported)
    assert fake.errors == []
    assert driver.captures == 1


def test_window_keyword_state_is_shared_between_pages(monkeypatch, project_root: Path) -> None:
    """“截图与框选”和“运行采集”两处输入框共用同一个会话值。"""

    fake = _install(monkeypatch, project_root, lambda image: None)
    fake.button_queue = [False, True, False, False]
    _run(project_root, "dsh_shared_keyword_smoke", rounds=5)

    labels = [item.get("label") for item in fake.text_inputs]
    assert labels.count("前端窗口标题关键字") >= 2, labels
    keys = {
        item.get("key")
        for item in fake.text_inputs
        if item.get("label") == "前端窗口标题关键字"
    }
    assert "window_title_capture" in keys
    assert "window_title_keyword" in fake.session_state
    # 两处输入框读到的默认值来自同一个状态：修改一处后另一处显示的默认值同步
    for item in fake.text_inputs:
        if item.get("label") == "前端窗口标题关键字":
            assert "value" in item

