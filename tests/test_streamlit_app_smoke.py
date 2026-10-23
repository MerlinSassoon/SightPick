"""前端空状态冒烟：用最小 Streamlit 替身执行 app，验证导入与顶层接线。

这不是 Streamlit 运行时测试；真实页面仍需安装依赖后人工验证（见 docs/RUNBOOK.md）。
"""

from __future__ import annotations

import runpy
import sys
import types
from pathlib import Path


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
    def __init__(self) -> None:
        super().__init__("streamlit")
        self.session_state = _FakeState()

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
        if name in ("expander", "spinner"):
            return lambda *args, **kwargs: _FakeCtx()
        if name == "button":
            return lambda *args, **kwargs: False
        if name in ("checkbox", "toggle"):
            return lambda *args, **kwargs: False
        if name == "text_input":
            return lambda *args, **kwargs: ""
        if name == "number_input":
            return lambda *args, **kwargs: 0
        if name in ("selectbox", "radio"):
            return lambda *args, **kwargs: (
                args[0][0] if args and isinstance(args[0], list) else None
            )
        if name == "multiselect":
            return lambda *args, **kwargs: []
        if name == "rerun":
            def rerun():
                raise SystemExit("rerun")

            return rerun
        return lambda *args, **kwargs: None


def test_streamlit_app_empty_state(monkeypatch, project_root: Path, tmp_path: Path) -> None:
    fake = _FakeStreamlit()
    fake.query_params = {}
    monkeypatch.setitem(sys.modules, "streamlit", fake)

    component = types.ModuleType("streamlit_image_coordinates")

    def streamlit_image_coordinates(image, *, key=None, click_and_drag=False):
        return None

    component.streamlit_image_coordinates = streamlit_image_coordinates
    monkeypatch.setitem(sys.modules, "streamlit_image_coordinates", component)
    monkeypatch.syspath_prepend(str(project_root / "src"))

    # 空运行目录：只验证“无运行记录”分支；真实运行目录的渲染见 test_streamlit_app_results_smoke
    import recruiting_capture.storage.reader as reader

    monkeypatch.setattr(reader, "list_runs", lambda root: [])
    runpy.run_path(
        str(project_root / "app" / "streamlit_app.py"), run_name="dsh_streamlit_smoke"
    )
