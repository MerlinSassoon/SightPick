"""配置档案区块的真实前端交互（Streamlit ``AppTest``）。

与 ``test_streamlit_app_smoke.py`` 的替身不同，本文件用 Streamlit 官方
``AppTest`` 真正执行 ``app/streamlit_app.py``，覆盖“保存 → 加载 → 删除”整条链路，
用来守住三类只在运行时才会暴露的问题：

1. 控件 key 与 value/default 共存导致默认值被静默忽略；
2. 删除档案时在控件实例化之后修改会话键（Streamlit 会抛异常）；
3. 加载档案后 builder 未真正替换（区域仍是被覆盖前的旧值）。

档案目录通过 ``RECRUITING_PROFILES_DIR`` 指向临时目录，不会污染 ``configs/profiles``。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("streamlit", reason="未安装 streamlit，跳过前端交互测试")

from streamlit.testing.v1 import AppTest  # noqa: E402

from recruiting_capture.models.plan_io import load_plan  # noqa: E402
from recruiting_capture.plan_builder import PlanBuilder  # noqa: E402


@pytest.fixture
def app(project_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppTest:
    """启动 app，并注入一个已配置好三个区域的 builder。"""

    profiles_dir = tmp_path / "profiles"
    monkeypatch.setenv("RECRUITING_PROFILES_DIR", str(profiles_dir))

    example = load_plan(project_root / "configs" / "boss_1080p_example.json")
    builder = PlanBuilder.from_plan(example)

    instance = AppTest.from_file(
        str(project_root / "app" / "streamlit_app.py"), default_timeout=90
    )
    instance.run()
    instance.session_state["builder"] = builder
    instance.run()
    return instance


def test_profile_section_renders_without_exception(app: AppTest) -> None:
    assert not app.exception
    assert app.button(key="profile_save") is not None
    assert app.button(key="profile_load_example") is not None
    # 页面里其它标签页也有 text_input（如窗口标题关键字），按 label 找而不是取第 0 个
    assert any(item.label == "档案名" for item in app.text_input)


def test_save_then_load_then_delete(app: AppTest, tmp_path: Path) -> None:
    profiles_dir = tmp_path / "profiles"

    # 1) 保存：默认档案名取自 builder.plan_name
    app.button(key="profile_save").click().run()
    assert not app.exception
    saved = list(profiles_dir.glob("*.json"))
    assert len(saved) == 1
    payload = json.loads(saved[0].read_text(encoding="utf-8"))
    assert payload["kind"] == "profile"
    assert "run" not in payload
    assert set(payload["regions"]) == {"A", "B", "C"}

    # 2) 列表出现后加载：builder 必须被替换成档案内容
    assert app.selectbox(key="profile_select").options == [saved[0].stem]
    app.session_state["builder"] = PlanBuilder(1920, 1080)  # 先清空，确认真的被覆盖
    app.run()
    app.button(key="profile_load").click().run()
    assert not app.exception
    assert list(app.session_state["builder"].regions) == ["A", "B", "C"]
    assert app.session_state["builder"].configs["A"]["invoke_regions"] == ["C", "B"]
    assert any("已加载档案" in item.value for item in app.success)

    # 3) 删除：先勾选确认，删除后文件消失且 selectbox 键被清掉
    app.checkbox(key="profile_confirm_delete").check().run()
    app.button(key="profile_delete").click().run()
    assert not app.exception
    assert not saved[0].exists()
    assert "profile_select" not in app.session_state


def test_delete_requires_confirmation(app: AppTest, tmp_path: Path) -> None:
    profiles_dir = tmp_path / "profiles"
    app.button(key="profile_save").click().run()
    saved = list(profiles_dir.glob("*.json"))
    assert saved

    app.button(key="profile_delete").click().run()
    assert not app.exception
    assert saved[0].exists()  # 未确认，不能删
    assert any("请先勾选" in item.value for item in app.warning)


def test_save_without_regions_reports_error(
    project_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RECRUITING_PROFILES_DIR", str(tmp_path / "profiles"))
    instance = AppTest.from_file(
        str(project_root / "app" / "streamlit_app.py"), default_timeout=90
    )
    instance.run()
    instance.session_state["builder"] = PlanBuilder(1920, 1080)  # 已截图、未框选
    instance.run()

    # 未框选时档案块仍要可见（这正是加载档案的主要场景）
    assert not instance.exception
    assert instance.button(key="profile_save") is not None

    instance.button(key="profile_save").click().run()
    assert not instance.exception
    assert list((tmp_path / "profiles").glob("*.json")) == []
    assert any("无法保存" in item.value for item in instance.error)
